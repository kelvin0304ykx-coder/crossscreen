"""Paired, duplex file channel. Only the receiving GUI chooses a destination."""
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import queue
import secrets
import socket
import ssl
import stat
import struct
import tempfile
import threading
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

CHUNK = 128 * 1024
LIMIT = 16 * 1024**3
MEDIA_TYPES = mimetypes.MimeTypes()


def is_media(name):
    suffix = Path(name).suffix.lower()
    # These extensions also identify TypeScript source files.
    if suffix in {".ts", ".mts", ".cts", ".tsx"}:
        return False
    mime, _ = MEDIA_TYPES.guess_type(str(name), strict=False)
    return ((mime is not None and mime.startswith(("image/", "video/")))
            or suffix in {".avif", ".heic", ".heif", ".m4v", ".mkv", ".m2ts"})


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


@dataclass
class ReceiveTarget:
    folder: Path
    count: int
    bytes_left: int | None = None
    paths: list[Path] = field(default_factory=list)

    @property
    def clipboard(self):
        return self.bytes_left is not None

    def discard(self):
        if self.clipboard:
            for path in self.paths:
                path.unlink(missing_ok=True)
            try:
                self.folder.rmdir()
            except OSError:
                pass


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
        self.clipboard_id = None
        self.clipboard_offer = None
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

    def offer(self, paths, press=None, *, clipboard=False):
        paths = tuple(Path(p) for p in paths)
        if not 1 <= len(paths) <= 64 or not all(p.is_file() and not p.is_symlink() and p.stat().st_size <= LIMIT for p in paths):
            raise ValueError("每次可传 1–64 个普通文件，单个文件最大 16 GiB")
        for p in paths:
            filename(p.name)
        token = secrets.token_hex(16)
        if clipboard:
            if not all(is_media(p.name) for p in paths):
                raise ValueError("文件剪贴板支持图片和视频文件")
            sizes = tuple(p.stat().st_size for p in paths)
            size = sum(sizes)
            if size > LIMIT:
                raise ValueError("一次复制的图片和视频总大小不能超过 16 GiB")
            self.revoke_clipboard()
            with self.lock:
                self.clipboard_id, self.clipboard_offer = token, tuple(zip(paths, sizes))
            if self.send({"op": "clip-offer", "id": token, "count": len(paths), "size": size}):
                return token
            self.revoke_clipboard(token)
            return None
        with self.lock:
            self.offers = {token: paths}
        if self.send({"op": "offer", "id": token, "count": len(paths), **({"press": press} if press else {})}):
            return token

    def revoke_clipboard(self, token=None):
        with self.lock:
            previous = self.clipboard_id
            if not previous or (token is not None and token != previous):
                return False
            self.clipboard_id = self.clipboard_offer = None
        self.send({"op": "clip-clear", "id": previous})
        return True

    def accept(self, token, folder, count, *, clipboard_size=None):
        if not folder.is_dir() or type(count) is not int or not 1 <= count <= 64:
            return False
        if clipboard_size is not None and (type(clipboard_size) is not int or not 0 <= clipboard_size <= LIMIT):
            return False
        with self.lock:
            if token in self.targets:
                return False
            self.targets[token] = ReceiveTarget(folder, count, clipboard_size)
        if self.send({"op": "accept", "id": token}):
            return True
        with self.lock:
            self.targets.pop(token, None)
        return False

    def clipboard_folders(self):
        with self.lock:
            return {target.folder for target in self.targets.values() if target.clipboard}

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
                        self.notify({"op":"status", "text":"文件通道中断，请重新复制或拖动"})
                    if not server:
                        self.done.wait(1)
                finally:
                    self.connected.clear()
                    self.socket = None
                    with self.lock:
                        self.targets.clear()
                        self.offers.clear()
                        self.clipboard_id = self.clipboard_offer = None
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
        active = None
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
                        if len(target.paths) >= target.count:
                            raise ValueError("文件数量不符")
                    if target.clipboard:
                        size = msg["size"]
                        if (type(size) is not int or not 0 <= size <= target.bytes_left
                                or not is_media(filename(msg["name"]))):
                            raise ValueError("文件剪贴板数据与复制请求不符")
                        target.bytes_left -= size
                    incoming = Incoming(target.folder, msg["name"], msg["size"])
                    active = token
                elif op == "end" and incoming and token == active:
                    path = incoming.finish(msg["sha"])
                    incoming = None
                    with self.lock:
                        target = self.targets[token]
                        target.paths.append(path)
                        complete = len(target.paths) == target.count
                        if complete:
                            if target.clipboard and target.bytes_left:
                                raise ValueError("文件剪贴板总大小不符")
                            del self.targets[token]
                    if target.clipboard:
                        if complete:
                            self.notify({"op": "clip-ready", "id": token, "paths": tuple(target.paths)})
                    else:
                        self.notify({"op":"status", "text":"已复制到 " + str(path)})
                        self.send({"op":"saved", "id":token})
                elif op == "accept":
                    with self.lock:
                        clipboard = token is not None and token == self.clipboard_id
                        if clipboard:
                            files, self.clipboard_offer = self.clipboard_offer, None
                        else:
                            files = self.offers.pop(token, None)
                    if files:
                        self.send((token, files, clipboard))
                elif op == "clip-clear":
                    with self.lock:
                        target = self.targets.get(token)
                        if target and target.clipboard:
                            del self.targets[token]
                        else:
                            target = None
                    if target:
                        if incoming and active == token:
                            incoming.discard()
                            incoming = None
                        target.discard()
                    self.notify(msg)
                elif op == "clip-decline":
                    if isinstance(token, str) and self.revoke_clipboard(token):
                        self.notify(msg)
                elif op in ("offer", "release", "cancel", "saved", "layout", "clip-offer", "clip-saved"):
                    self.notify(msg)
                elif op == 'clip-content' and self.config.input_mode == 'duplex':
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
            with self.lock:
                targets = tuple(self.targets.values())
            for target in targets:
                target.discard()

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
        waiting = deque()
        active = None
        try:
            while True:
                try:
                    job = self.out.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if isinstance(job, dict):
                        yield b"J", json.dumps(job).encode()
                        continue
                    waiting.append(job)
                if active is None and waiting:
                    active = self.file_frames(*waiting.popleft())
                if active is None:
                    yield b"J", b'{"op":"ping"}'
                    continue
                try:
                    yield next(active)
                except StopIteration:
                    active = None
        finally:
            if active is not None:
                active.close()

    def file_frames(self, token, files, clipboard):
        try:
            for item in files:
                path, expected_size = item if clipboard else (item, None)
                if clipboard and token != self.clipboard_id:
                    return
                original = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(original.st_mode):
                    raise ValueError("复制的文件已变化")
                with path.open("rb") as stream:
                    current = os.fstat(stream.fileno())
                    if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                        raise ValueError("复制的文件已变化")
                    if clipboard and current.st_size != expected_size:
                        raise ValueError("复制的文件大小已变化")
                    begin = {"op": "begin", "id": token, "name": path.name, "size": current.st_size}
                    yield b"J", json.dumps(begin).encode()
                    digest = hashlib.sha256()
                    remaining = current.st_size
                    while remaining:
                        if clipboard and token != self.clipboard_id:
                            return
                        block = stream.read(min(CHUNK, remaining))
                        if not block:
                            raise ValueError("复制的文件大小已变化")
                        remaining -= len(block)
                        digest.update(block)
                        yield b"B", block
                    if clipboard and token != self.clipboard_id:
                        return
                    after = os.fstat(stream.fileno())
                    if (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
                        raise ValueError("复制的文件在传送时已变化")
                    yield b"J", json.dumps({"op": "end", "id": token, "sha": digest.hexdigest()}).encode()
        except (OSError, ValueError):
            if not clipboard:
                raise
            if self.revoke_clipboard(token):
                self.notify({"op": "clip-failed", "id": token})
