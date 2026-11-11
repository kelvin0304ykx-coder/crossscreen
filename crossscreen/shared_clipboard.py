"""Text, rich text and bitmap clipboard on the existing file channel.

Media file URLs remain owned by FileClipboard. No clipboard data is logged.
"""
import base64
from collections import deque
import json
import re
import secrets
import sys
import time

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtWidgets import QApplication

LIMIT = 4 * 1024 * 1024
CHUNK = 32768
MARKER = 'application/x-crossscreen-duplex'
TYPES = ('text/plain','image/png','application/x-crossscreen-rich')


class ContentAssembler:
    def __init__(self):
        self.current = None

    def feed(self, msg, revision):
        token, kind = msg.get('id'), msg.get('type')
        index, count, data = msg.get('index'), msg.get('count'), msg.get('data')
        if (not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token) or kind not in TYPES
                or type(index) is not int or type(count) is not int or not 0 <= index < count <= LIMIT//CHUNK
                or not isinstance(data,str) or len(data) > 44000):
            raise ValueError('剪贴板片段无效')
        block = base64.b64decode(data, validate=True)
        if len(block) > CHUNK:
            raise ValueError('剪贴板片段过长')
        if index == 0:
            self.current = [token,kind,count,revision,0,bytearray(),time.monotonic()]
        current = self.current
        if not current or current[:4] != [token,kind,count,revision] or index != current[4]:
            self.current = None
            return None
        if time.monotonic() - current[6] > 30:
            self.current = None
            return None
        current[5].extend(block)
        current[4] += 1
        if current[4] == count:
            self.current = None
            return kind, bytes(current[5])


class SharedClipboard:
    def __init__(self, link, enabled, notice, *, revision_reader=None):
        self.link, self.local_enabled, self.notice = link, enabled, notice
        self.enabled = False
        self.board = QApplication.clipboard()
        if revision_reader is None and sys.platform == 'darwin':
            from .native_clipboard import NativeClipboard
            native = None
            def revision_reader():
                nonlocal native
                if native is None:
                    native = NativeClipboard()
                return native.revision()
        self.revision_reader = revision_reader
        self.native_revision = None
        if self.revision_reader is not None:
            try:
                self.native_revision = self.revision_reader()
            except (OSError, RuntimeError):
                pass
        self.revision = 0
        self.applying = False
        self.dirty = False
        self.out = deque()
        self.assembler = ContentAssembler()
        self.board.dataChanged.connect(self.changed)

    def stop(self):
        self.board.dataChanged.disconnect(self.changed)
        self.out.clear()

    def changed(self):
        if self.revision_reader is not None:
            try:
                revision = self.revision_reader()
            except (OSError, RuntimeError):
                return False
            previous, self.native_revision = self.native_revision, revision
            if previous is None or previous == revision:
                return True
        self.revision += 1
        if not self.applying:
            self.dirty = True
            self.out.clear()
        return True

    def observe_native(self):
        # Cocoa's Qt dataChanged notification depends on app activation.
        # Poll only the native serial; read image/text bytes only on a change.
        return self.changed() if self.revision_reader is not None else True

    def poll(self):
        if not self.observe_native():
            return
        if not self.enabled or not self.link.connected.is_set():
            self.dirty = False
            self.out.clear()
            return
        if self.dirty:
            self.dirty = False
            mime = self.board.mimeData()
            if mime is None or mime.hasFormat(MARKER):
                return
            # Browsers may include the HTTP source URL alongside copied pixels.
            # Only local file references belong to the media file channel.
            if mime.hasUrls() and any(url.isLocalFile() for url in mime.urls()):
                return
            if mime.hasImage():
                image = self.board.image()
                if image.width()*image.height() > 16_000_000:
                    self.notice('图片像素过多，请复制图片文件传送')
                    return
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.WriteOnly)
                if not image.save(buffer,'PNG'):
                    return
                kind, payload = 'image/png',bytes(data)
            elif mime.hasHtml():
                kind = 'application/x-crossscreen-rich'
                payload = json.dumps({'text':mime.text(),'html':mime.html()},ensure_ascii=False).encode()
            elif mime.hasText():
                kind,payload = 'text/plain',mime.text().encode('utf-8')
            else:
                return
            if len(payload) > LIMIT:
                self.notice('剪贴板内容超过 4 MB，请改用文件复制')
                return
            token = secrets.token_hex(16)
            blocks = [payload[i:i+CHUNK] for i in range(0,len(payload),CHUNK)] or [b'']
            self.out.extend(dict(op='clip-content',id=token,type=kind,index=i,count=len(blocks),
                                 data=base64.b64encode(block).decode()) for i,block in enumerate(blocks))
        for _ in range(4):
            if not self.out or not self.link.send(self.out[0]):
                break
            self.out.popleft()

    def message(self, msg):
        if msg.get('op') == 'layout' and msg.get('clipboard_files') == 1:
            self.enabled = self.local_enabled and msg.get('clipboard_enabled') is True
        elif msg.get('op') == 'offline':
            self.enabled = False
            self.out.clear()
            self.assembler = ContentAssembler()
        if not self.enabled or msg.get('op') != 'clip-content':
            return
        if not self.observe_native():
            self.assembler = ContentAssembler()
            return
        try:
            result = self.assembler.feed(msg,self.revision)
            if result is None:
                return
            kind, data = result
            mime = QMimeData()
            mime.setData(MARKER,b'1')
            if kind == 'image/png':
                buffer = QBuffer()
                buffer.setData(data)
                buffer.open(QIODevice.ReadOnly)
                reader = QImageReader(buffer,b'PNG')
                size = reader.size()
                if not size.isValid() or size.width()*size.height() > 16_000_000:
                    raise ValueError('图片尺寸无效')
                image = reader.read()
                if image.isNull():
                    raise ValueError('图片解码失败')
                mime.setImageData(image)
            elif kind == 'text/plain':
                mime.setText(data.decode('utf-8'))
            else:
                rich = json.loads(data)
                if not isinstance(rich,dict) or not all(isinstance(rich.get(k),str) for k in ('text','html')):
                    raise ValueError('富文本无效')
                mime.setText(rich['text'])
                mime.setHtml(rich['html'])
            self.applying = True
            self.board.setMimeData(mime)
            if self.revision_reader is not None:
                self.native_revision = self.revision_reader()
            self.dirty = False
            self.out.clear()
        except (OSError, RuntimeError):
            self.notice('本机剪贴板暂不可用，请重新复制图片')
        except (ValueError,TypeError,UnicodeError):
            self.notice('对端剪贴板内容无效，已保留本机剪贴板')
        finally:
            self.applying = False
