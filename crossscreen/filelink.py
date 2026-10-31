"""Paired, duplex file channel. Only the receiving GUI chooses a destination."""
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import ssl
import struct
import tempfile
import threading
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

CHUNK = 128 * 1024
LIMIT = 16 * 1024**3


def read_exact(sock, n):
    result = bytearray()
    while len(result) < n:
        block = sock.recv(n - len(result))
        if not block:
            raise ConnectionError("文件通道已断开")
        result.extend(block)
    return bytes(result)


def frame(sock, kind, data):
    sock.sendall(kind + struct.pack("!I", len(data)) + data)


def packet(sock):
    header = read_exact(sock, 5)
    size = struct.unpack("!I", header[1:])[0]
    if size > CHUNK:
        raise ValueError("传输数据过长")
    return header[:1], read_exact(sock, size)


def authenticate(raw, pem, fingerprint, server):
    public = x509.load_pem_x509_certificate(pem.read_bytes()).public_bytes(Encoding.DER)
    frame(raw, b"C", public)
    kind, peer = packet(raw)
    if kind != b"C" or hashlib.sha256(peer).hexdigest() != fingerprint:
        raise ValueError("文件通道的配对指纹不匹配")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.load_cert_chain(str(pem))
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(peer))
    ctx.verify_flags = ssl.VERIFY_DEFAULT
    ctx.verify_mode = ssl.CERT_REQUIRED
    tls = ctx.wrap_socket(raw, server_side=server)
    if tls.getpeercert(binary_form=True) != peer:
        tls.close()
        raise ValueError("文件通道证书已变化")
    return tls


def filename(value):
    if (not isinstance(value, str) or not value or len(value.encode()) > 250
            or value in (".", "..") or value.endswith((" ", "."))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
            or value.partition(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                *("COM%d" % i for i in range(1, 10)), *("LPT%d" % i for i in range(1, 10))}):
        raise ValueError("文件名无法在两台电脑上使用")
    return value


class Incoming:
    def __init__(self, folder, name, size):
        self.name = Path(filename(name))
        if type(size) is not int or not 0 <= size <= LIMIT:
            raise ValueError("文件大小超出限制")
        self.folder = folder
        self.left = size
        self.hash = hashlib.sha256()
        self.file = tempfile.NamedTemporaryFile(dir=folder, prefix=".crossscreen-", delete=False)
        self.temp = Path(self.file.name)

    def append(self, block):
        if len(block) > self.left:
            raise ValueError("文件大小不符")
        self.file.write(block)
        self.hash.update(block)
        self.left -= len(block)

    def finish(self, digest):
        if self.left or self.hash.hexdigest() != digest:
            raise ValueError("文件校验失败")
        self.file.close()
        for i in range(10000):
            target = self.folder / (self.name.name if not i else f"{self.name.stem} ({i}){self.name.suffix}")
            try:
                os.link(self.temp, target)
                self.temp.unlink()
                return target
            except FileExistsError:
                continue
        raise ValueError("同名文件过多")

    def discard(self):
        self.file.close()
        self.temp.unlink(missing_ok=True)


class FileLink:
    def __init__(self, config, pem, notify):
        self.config = config
        self.pem = pem
        self.notify = notify
        self.done = threading.Event()
        self.connected = threading.Event()
        self.out = queue.Queue(256)
        self.socket = None
        self.listener = None
        self.offers = {}
        self.targets = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def send(self, msg):
        if not self.connected.is_set():
            return False
        try:
            self.out.put_nowait(msg)
            return True
        except queue.Full:
            return False

    def offer(self, paths, press=None):
        paths = tuple(Path(p) for p in paths)
        if not 1 <= len(paths) <= 64 or not all(p.is_file() and not p.is_symlink() and p.stat().st_size <= LIMIT for p in paths):
            raise ValueError("每次可拖 1–64 个普通文件，单个文件最大 16 GiB")
        for p in paths:
            filename(p.name)
        token = secrets.token_hex(16)
        with self.lock:
            self.offers = {token: paths}
        if self.send({"op": "offer", "id": token, "count": len(paths), **({"press": press} if press else {})}):
            return token

    def accept(self, token, folder, count):
        if not folder.is_dir() or not 1 <= count <= 64:
            return
        with self.lock:
            self.targets[token] = [folder, count, 0]
        self.send({"op": "accept", "id": token})

    def stop(self):
        self.done.set()
        if self.listener:
            self.listener.close()
        sock = self.socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        server = self.config.role == "server"
        port = self.config.port + (1 if self.config.port < 65535 else -1)
        listener = None
        try:
            if server:
                listener = socket.socket()
                self.listener = listener
                listener.bind((self.config.host, port))
                listener.listen(2)
                listener.settimeout(.5)
            while not self.done.is_set():
                try:
                    raw = listener.accept()[0] if server else socket.create_connection((self.config.host, port), 3)
                    with raw:
                        raw.settimeout(5)
                        with authenticate(raw, self.pem, self.config.peer_fingerprint, server) as sock:
                            self.session(sock)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    if self.connected.is_set():
                        self.notify({"op":"status", "text":"文件通道中断，请重新拖动"})
                    if not server:
                        self.done.wait(1)
                finally:
                    self.connected.clear()
                    self.socket = None
                    with self.lock:
                        self.targets.clear()
                        self.offers.clear()
        except OSError:
            if not self.done.is_set():
                self.notify({"op":"status", "text":"文件端口无法监听，请检查相邻端口"})
        finally:
            if listener:
                listener.close()

    def session(self, sock):
        sock.settimeout(45)
        self.socket = sock
        self.out = queue.Queue(256)
        self.connected.set()
        self.notify({"op":"online"})
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        incoming = None
        try:
            for kind, block in self.exchange(sock):
                if kind == b"B" and incoming:
                    incoming.append(block)
                    continue
                if kind != b"J":
                    raise ValueError("无效数据")
                msg = json.loads(block)
                op, token = msg["op"], msg.get("id")
                if op == "ping":
                    continue
                if op == "begin" and not incoming:
                    with self.lock:
                        target = self.targets[token]
                        if target[2] >= target[1]:
                            raise ValueError("文件数量不符")
                        target[2] += 1
                    incoming = Incoming(target[0], msg["name"], msg["size"])
                    active = token
                elif op == "end" and incoming and token == active:
                    path = incoming.finish(msg["sha"])
                    incoming = None
                    self.notify({"op":"status", "text":"已复制到 " + str(path)})
                    self.send({"op":"saved", "id":token})
                    with self.lock:
                        target = self.targets[token]
                        if target[1] == target[2]:
                            del self.targets[token]
                elif op == "accept":
                    with self.lock:
                        files = self.offers.pop(token, None)
                    if files:
                        self.send((token, files))
                elif op in ("offer", "release", "cancel", "saved", "layout"):
                    self.notify(msg)
                else:
                    raise ValueError("无效的传输顺序")
        finally:
            self.connected.clear()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.notify({"op":"offline"})
            if incoming:
                incoming.discard()

    def exchange(self, sock):
        # A single thread owns each TLS object. Alternating bounded frames lets
        # both machines send simultaneously without concurrent OpenSSL calls.
        outgoing = self.outgoing()
        try:
            while not self.done.is_set():
                if self.config.role == "server":
                    yield packet(sock)
                    frame(sock, *next(outgoing))
                else:
                    sent = next(outgoing)
                    frame(sock, *sent)
                    reply = packet(sock)
                    yield reply
                    if sent == reply == (b"J", b'{"op":"ping"}'):
                        self.done.wait(.015)
        finally:
            outgoing.close()

    def outgoing(self):
        def control(msg):
            return b"J", json.dumps(msg).encode()
        while True:
            try:
                job = self.out.get_nowait()
            except queue.Empty:
                yield b"J", b'{"op":"ping"}'
                continue
            if isinstance(job, dict):
                yield control(job)
                continue
            token, files = job
            for path in files:
                with path.open("rb") as stream:
                    yield control({"op":"begin", "id":token, "name":path.name, "size":os.fstat(stream.fileno()).st_size})
                    digest = hashlib.sha256()
                    while block := stream.read(CHUNK):
                        digest.update(block)
                        yield b"B", block
                    yield control({"op":"end", "id":token, "sha":digest.hexdigest()})
