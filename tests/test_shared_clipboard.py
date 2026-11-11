import base64
import pytest


def test_content_fragments_only_publish_complete_current_clipboard():
    from crossscreen.shared_clipboard import ContentAssembler
    a = ContentAssembler()
    def msg(index, data):
        return dict(op='clip-content', id='a'*32, type='text/plain', index=index,
                    count=2, data=base64.b64encode(data).decode())
    assert a.feed(msg(0,b'hello '), 4) is None
    assert a.feed(msg(1,b'world'), 4) == ('text/plain',b'hello world')
    assert a.feed(msg(0,b'old '), 4) is None
    assert a.feed(msg(1,b'copy'), 5) is None


def test_invalid_clipboard_fragment_cannot_allocate_unbounded_data():
    from crossscreen.shared_clipboard import ContentAssembler
    a = ContentAssembler()
    with pytest.raises(ValueError):
        a.feed(dict(op='clip-content',id='a'*32,type='text/plain',index=0,count=999999,data=''),0)


class ChangeSignal:
    def connect(self, callback): self.callback = callback
    def disconnect(self, callback): pass


class ClipboardBoard:
    def __init__(self):
        from PySide6.QtCore import QMimeData
        self.dataChanged = ChangeSignal()
        self.mime = QMimeData()
        self.serial = 10
    def mimeData(self): return self.mime
    def image(self): return self.mime.imageData()
    def setMimeData(self, mime):
        self.mime = mime
        self.serial += 1
        self.dataChanged.callback()


def content_bridge(monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    import crossscreen.shared_clipboard as module
    board = ClipboardBoard()
    monkeypatch.setattr(module, 'QApplication', SimpleNamespace(clipboard=lambda:board))
    messages = []
    online = Event()
    online.set()
    link = SimpleNamespace(connected=online, send=lambda msg:messages.append(msg) or True)
    bridge = module.SharedClipboard(link, True, lambda _:None, revision_reader=lambda:board.serial)
    bridge.message(dict(op='layout',clipboard_files=1,clipboard_enabled=True))
    return bridge, board, messages


def test_background_native_image_change_is_sent_without_qt_notification(monkeypatch):
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QMimeData
    bridge, board, messages = content_bridge(monkeypatch)
    bridge.poll()
    assert not messages  # starting sharing does not transmit old clipboard contents
    image = QImage(3,2,QImage.Format_ARGB32)
    image.fill(QColor('#13ab42'))
    board.mime = QMimeData()
    board.mime.setImageData(image)
    board.serial += 1  # external app changed macOS pasteboard; no Qt signal
    bridge.poll()
    assert messages and all(m['type'] == 'image/png' for m in messages)
    payload = b''.join(base64.b64decode(m['data']) for m in messages)
    decoded = QImage.fromData(payload,'PNG')
    assert decoded.size() == image.size()
    assert decoded.pixelColor(0,0) == image.pixelColor(0,0)
    count = len(messages)
    bridge.poll()
    assert len(messages) == count


def test_background_local_copy_wins_over_inflight_remote_clipboard(monkeypatch):
    bridge, board, messages = content_bridge(monkeypatch)
    def fragment(index, data):
        return dict(op='clip-content',id='b'*32,type='text/plain',index=index,count=2,
                    data=base64.b64encode(data).decode())
    bridge.message(fragment(0,b'old '))
    board.mime.setText('new local copy')
    board.serial += 1
    bridge.message(fragment(1,b'remote copy'))
    assert board.mime.text() == 'new local copy'


def test_received_clipboard_does_not_echo_when_native_counter_changes(monkeypatch):
    bridge, board, messages = content_bridge(monkeypatch)
    bridge.message(dict(op='clip-content',id='c'*32,type='text/plain',index=0,count=1,
                        data=base64.b64encode(b'remote').decode()))
    assert board.mime.text() == 'remote'
    bridge.poll()
    assert not messages


def test_web_image_with_source_url_is_still_sent_as_image(monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QColor
    bridge, board, messages = content_bridge(monkeypatch)
    image = QImage(2,2,QImage.Format_ARGB32)
    image.fill(QColor('blue'))
    board.mime.setImageData(image)
    board.mime.setUrls([QUrl('https://example.org/image.png')])
    board.serial += 1
    bridge.poll()
    assert messages and messages[0]['type'] == 'image/png'


def test_image_file_url_remains_on_file_transfer_channel(monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage
    bridge, board, messages = content_bridge(monkeypatch)
    board.mime.setImageData(QImage(2,2,QImage.Format_ARGB32))
    board.mime.setUrls([QUrl.fromLocalFile('/sample.png')])
    board.serial += 1
    bridge.poll()
    assert not messages


def test_native_pasteboard_temporarily_unavailable_does_not_break_start(monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    import crossscreen.shared_clipboard as module
    import crossscreen.native_clipboard as native
    board = ClipboardBoard()
    attempts = []
    def construct():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError('pasteboard temporarily unavailable')
        return SimpleNamespace(revision=lambda:board.serial)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(module, 'QApplication', SimpleNamespace(clipboard=lambda:board))
    monkeypatch.setattr(native, 'NativeClipboard', construct)
    messages = []
    online = Event(); online.set()
    link = SimpleNamespace(connected=online,send=lambda m:messages.append(m) or True)
    bridge = module.SharedClipboard(link,True,lambda _:None)
    bridge.message(dict(op='layout',clipboard_files=1,clipboard_enabled=True))
    bridge.poll()
    assert not messages
    board.mime.setText('new copy after recovery')
    board.serial += 1
    bridge.poll()
    assert messages and messages[0]['type'] == 'text/plain'
