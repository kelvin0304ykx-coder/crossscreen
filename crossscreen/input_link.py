"""Bounded low-latency channel, authenticated with existing paired identities.

One thread owns TLS I/O. File transfer never queues ahead of pointer events.
"""
import json
import queue
import select
import socket
import struct
import sys
import threading
import time

from .filelink import authenticate

MAX_FRAME = 65536
PROTOCOL = 'crossscreen-input/1'


def encode_frame(msg):
    data = json.dumps(msg, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_FRAME:
        raise ValueError('输入消息过长')
    return struct.pack('!I', len(data)) + data


def decode_frames(buffer):
    messages = []
    while len(buffer) >= 4:
        size = struct.unpack_from('!I', buffer)[0]
        if not 0 < size <= MAX_FRAME:
            raise ValueError('输入消息长度无效')
        if len(buffer) < size + 4:
            break
        msg = json.loads(buffer[4:size+4])
        del buffer[:size+4]
        if not isinstance(msg, dict) or not isinstance(msg.get('op'), str):
            raise ValueError('输入消息格式无效')
        messages.append(msg)
    return messages


class InputLink:
    def __init__(self, config, pem, notify):
        self.config, self.pem, self.notify = config, pem, notify
        self.done = threading.Event()
        self.connected = threading.Event()
        self.out = queue.Queue(512)
        self.socket = self.listener = None
        self.generation = 0
        self.thread = threading.Thread(target=self.run, name='CrossScreen-input', daemon=True)

    def start(self):
        self.thread.start()

    def send(self, msg):
        if not self.connected.is_set():
            raise ConnectionError('键鼠通道尚未连接')
        try:
            self.out.put_nowait(encode_frame(msg))
        except queue.Full as exc:
            self.disconnect()
            raise ConnectionError('输入通道拥塞，已恢复本机') from exc

    def disconnect(self):
        self.connected.clear()
        stream = self.socket
        if stream:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()

    def stop(self):
        self.done.set()
        self.disconnect()
        if self.listener:
            self.listener.close()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(2)

    def run(self):
        server = self.config.role == 'server'
        while not self.done.is_set():
            try:
                if server:
                    if self.listener is None:
                        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        self.listener = listener
                        # Windows SO_REUSEADDR could hijack another listener.
                        if sys.platform == 'win32':
                            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                        else:
                            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        listener.bind((self.config.host, self.config.port))
                        listener.listen(2)
                        listener.settimeout(.3)
                    try:
                        raw, _ = self.listener.accept()
                    except socket.timeout:
                        continue
                else:
                    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.socket = raw
                    raw.settimeout(2)
                    raw.connect((self.config.host, self.config.port))
                self.socket = raw
                raw.settimeout(2)
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                stream = authenticate(raw, self.pem, self.config.peer_fingerprint, server)
                self.socket = stream
                self.generation += 1
                self._session(stream, server)
            except (OSError, ValueError, ConnectionError, queue.Full, RuntimeError):
                # Do not log payloads, host certificates, or key events.
                pass
            finally:
                self.disconnect()
                self.socket = None
                while True:
                    try:
                        self.out.get_nowait()
                    except queue.Empty:
                        break
                if not self.done.is_set():
                    try:
                        self.notify({'op':'offline','_generation':self.generation})
                    except queue.Full:
                        pass
            self.done.wait(.5)
        if self.listener:
            self.listener.close()
            self.listener = None

    def _session(self, stream, server):
        stream.sendall(encode_frame({'op':'hello','protocol':PROTOCOL, 'role':self.config.role,
                                    'side':self.config.side, 'clipboard':self.config.clipboard}))
        buffer = bytearray()
        last_rx = last_ping = time.monotonic()
        ready = False
        stream.settimeout(.15)
        while not self.done.is_set():
            now = time.monotonic()
            if now - last_rx > 2:
                raise TimeoutError('键鼠通道超时')
            if now - last_ping > .4:
                stream.sendall(encode_frame({'op':'ping'}))
                last_ping = now
            # Limit each write batch so inbound data gets equal service.
            for _ in range(32):
                try:
                    data = self.out.get_nowait()
                except queue.Empty:
                    break
                stream.sendall(data)
            if not stream.pending() and not select.select([stream], [], [], .004)[0]:
                continue
            data = stream.recv(16384)
            if not data:
                raise ConnectionError('键鼠通道已断开')
            buffer.extend(data)
            for msg in decode_frames(buffer):
                last_rx = time.monotonic()
                op = msg['op']
                if not ready:
                    if (op != 'hello' or msg.get('protocol') != PROTOCOL or
                            msg.get('role') != ('client' if server else 'server') or
                            msg.get('side') not in ('left','right') or type(msg.get('clipboard')) is not bool):
                        raise ValueError('两端输入协议版本或角色不匹配')
                    ready = True
                    self.connected.set()
                    self.notify({'op':'online','side':self.config.side if server else msg['side'],
                                 'clipboard':self.config.clipboard and msg['clipboard'],
                                 '_generation':self.generation})
                elif op == 'ping':
                    continue
                elif op in ('hello','online','offline'):
                    raise ValueError('重复或无效握手')
                else:
                    self.notify({**msg, '_generation':self.generation})
