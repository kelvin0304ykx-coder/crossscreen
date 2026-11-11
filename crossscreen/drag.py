import ctypes
import secrets
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QLabel

from .clipboard import FileClipboard
from .drop_target import folder_at
from .filelink import FileLink
from .finder_drag import FinderDragBoard, FinderGesture, FinderInput, finish_handoff
from .native_clipboard import NativeClipboard


class HostPress:
    """One physical press on the shared mouse; consume the drop at most once."""
    def __init__(self):
        self.begin(False)

    def begin(self, from_mac):
        self.id = secrets.token_hex(16) if from_mac else None
        self.pending = None
        return self.id

    def defer(self, folder, now):
        if self.id:
            self.pending = (folder, now)

    def take(self, press, now):
        if self.pending and self.id == press:
            folder, start = self.pending
            self.begin(False)
            if now - start <= 1:
                return folder


class Edge(QLabel):
    def __init__(self, bridge):
        super().__init__("拖过这里\n到对面松手", None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.bridge = bridge
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#245fda;color:white;border-radius:8px;padding:4px")

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(u.isLocalFile() for u in urls):
            try:
                self.bridge.arm([Path(u.toLocalFile()) for u in urls])
                event.setDropAction(Qt.CopyAction)
                event.accept()
            except (ValueError, OSError) as exc:
                self.bridge.notice.emit(str(exc))

    def dropEvent(self, event):
        # Dropping on the source strip itself never completes a transfer.
        event.ignore()


class RawInput(QAbstractNativeEventFilter):
    """Observe hardware release on the USB host, even while Deskflow swallows mouse events."""
    def __init__(self, bridge, hwnd):
        super().__init__()
        self.bridge = bridge
        class Device(ctypes.Structure):
            _fields_ = [("page", ctypes.c_ushort), ("usage", ctypes.c_ushort),
                        ("flags", wintypes.DWORD), ("window", wintypes.HWND)]
        devices = (Device * 2)(Device(1, 2, 0x100, hwnd), Device(1, 6, 0x100, hwnd))
        if not ctypes.windll.user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(Device)):
            raise OSError("无法监听拖动松手")

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0xff:
            header = 8 + 2 * ctypes.sizeof(ctypes.c_void_p)
            buf = ctypes.create_string_buffer(256)
            size = ctypes.c_uint(len(buf))
            n = ctypes.windll.user32.GetRawInputData(ctypes.c_void_p(msg.lParam), 0x10000003,
                        buf, ctypes.byref(size), header)
            if n >= header + 12:
                data = buf.raw
                kind = int.from_bytes(data[:4], "little")
                if kind == 0:
                    flags = int.from_bytes(data[header+4:header+6], "little")
                    if flags & 1:
                        self.bridge.pressed()
                    if flags & 2:
                        self.bridge.released()
                if kind == 1 and int.from_bytes(data[header+6:header+8], "little") == 27:
                    self.bridge.cancel()
        return False


class DragBridge(QObject):
    received = Signal(object)
    notice = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.link = None
        self.clipboard = None
        self.content = None
        self.remote = self.local = None
        self.on_mac = False
        self.host_press = HostPress()
        self.peer_press = None
        self.duplex_drop = None
        self.edge = Edge(self)
        self.received.connect(self.message)
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.edge_tick)
        self.raw = None
        self.board = None
        self.gesture = FinderGesture()
        if sys.platform == "darwin":
            self.board = FinderDragBoard()
            self.cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            self.cg.CGEventSourceButtonState.restype = ctypes.c_bool
        elif sys.platform == "win32":
            self.raw = RawInput(self, int(window.winId()))
            QApplication.instance().installNativeEventFilter(self.raw)

    def start(self, config, root):
        self.stop()
        self.config = config
        self.duplex = config.input_mode == 'duplex'
        self.on_mac = sys.platform == 'darwin' if self.duplex else False
        self.shared_side = config.side
        link = FileLink(config, root / "tls/identity.pem", lambda msg: self.received.emit((link, msg)))
        self.link = link
        if self.duplex:
            from .shared_clipboard import SharedClipboard
            self.content = SharedClipboard(link, config.clipboard, self.notice.emit)
        try:
            board = NativeClipboard(int(self.window.winId()))
            self.clipboard = FileClipboard(link, config, root.parent / "clipboard-files", board, self.notice.emit)
        except (ImportError, OSError, RuntimeError):
            self.notice.emit("本机文件剪贴板暂不可用")
        link.thread.start()
        self.timer.start()

    def stop(self):
        self.host_press.begin(False)
        self.peer_press = None
        self.duplex_drop = None
        if self.content:
            self.content.stop()
            self.content = None
        self.timer.stop()
        self.edge.hide()
        if self.link:
            self.link.stop()
            self.link = None
        self.clipboard = None
        self.remote = self.local = None
        self.on_mac = False

    def arm(self, paths):
        if self.link and not self.local:
            self.local = self.link.offer(paths, self.peer_press if self.board is not None and not self.duplex else None)
            if self.local:
                self.notice.emit("继续拖到对面的文件夹窗口，再松开鼠标")

    def screen_changed(self, mac):
        if self.clipboard is not None and mac != self.on_mac:
            self.clipboard.switched(mac == (sys.platform == "darwin"))
        if mac and not self.on_mac:
            self.host_press.begin(False)
            self.peer_press = None
        if self.board is not None and self.link and mac != self.on_mac and not self.duplex:
            if mac:
                self.gesture.enter(self.board.revision())
            elif self.gesture.take(self.board.revision(), self.cg.CGEventSourceButtonState(0, 0)):
                if not self.remote and self.link.connected.is_set():
                    try:
                        files = self.board.files()
                        if files:
                            self.arm(files)
                            if self.local:
                                link = self.link
                                finish_handoff(FinderInput(), lambda f: QTimer.singleShot(120, f),
                                               lambda: self.link is link and not self.on_mac)
                    except (OSError, ValueError) as exc:
                        self.notice.emit(str(exc))
        self.on_mac = mac

    def cancel(self):
        self.host_press.begin(False)
        self.peer_press = None
        if self.link and (self.local or self.remote):
            self.link.send({"op":"cancel", "id": self.local or self.remote[0]})
        self.remote = self.local = None

    def pressed(self):
        if getattr(self, 'duplex', False):
            return
        serial = self.host_press.begin(bool(self.on_mac and self.link))
        if serial:
            self.link.send({"op": "layout", "press": serial})

    def released(self):
        if getattr(self, 'duplex', False):
            return
        if self.link and self.host_press.id and not self.on_mac and not (self.local or self.remote):
            import win32api
            try:
                self.host_press.defer(folder_at(win32api.GetCursorPos()), time.monotonic())
            except Exception:
                self.host_press.begin(False)
            return
        self.host_press.begin(False)
        if not self.link or not (self.local or self.remote):
            return
        if self.local and self.on_mac:
            self.link.send({"op":"release", "id":self.local})
            self.local = None
        elif self.remote and not self.on_mac:
            self.receive_here()
        else:
            self.cancel()

    def receive_here(self):
        token, count = self.remote
        self.remote = None
        self.edge.hide()
        try:
            if sys.platform == "win32":
                import win32api
                point = win32api.GetCursorPos()
            else:
                pos = QCursor.pos()
                point = (pos.x(), pos.y())
            folder = folder_at(point)
            if folder:
                self.link.accept(token, folder, count)
                self.notice.emit("正在复制到 " + str(folder))
                return
        except ValueError as exc:
            self.link.send({"op":"cancel", "id":token})
            self.notice.emit(str(exc))
            return
        except Exception:
            pass
        self.link.send({"op":"cancel", "id":token})
        self.notice.emit("没有识别到目标文件夹，请拖到已打开文件夹的内容区再松手")

    def message(self, event):
        link, msg = event
        if link is not self.link:
            return
        op = msg["op"]
        if self.content is not None:
            self.content.message(msg)
        if self.clipboard is not None:
            self.clipboard.message(msg)
        if op.startswith("clip-"):
            return
        if op == "layout" and self.config.role == "client":
            if msg.get("side") in ("left", "right"):
                self.shared_side = msg["side"]
            if isinstance(msg.get("press"), str) and len(msg["press"]) == 32:
                self.peer_press = msg["press"]
        elif op == "offer":
            if self.duplex and self.duplex_drop and time.monotonic() - self.duplex_drop[1] <= 1:
                folder, _ = self.duplex_drop
                self.duplex_drop = None
                if folder:
                    self.link.accept(msg['id'], folder, msg['count'])
                else:
                    self.link.send({'op':'cancel','id':msg['id']})
                return
            if self.config.role == "server" and msg.get("press"):
                if msg["press"] != self.host_press.id or self.host_press.pending:
                    folder = self.host_press.take(msg["press"], time.monotonic())
                    if folder:
                        self.link.accept(msg["id"], folder, msg["count"])
                        self.notice.emit("正在复制到 " + str(folder))
                    else:
                        self.link.send({"op":"cancel", "id":msg["id"]})
                    return
            self.remote = msg["id"], msg["count"]
            self.edge.hide()
        elif op == "release" and self.remote and msg["id"] == self.remote[0]:
            self.receive_here()
        elif op == "cancel":
            self.remote = self.local = None
            self.notice.emit("此次拖动已取消")
        elif op == "saved":
            self.local = None
            self.notice.emit("文件已复制到对面的文件夹")
        elif op == "status":
            self.local = self.remote = None
            self.notice.emit(msg["text"])
        elif op == "offline":
            self.remote = self.local = None
            self.notice.emit("文件通道已断开，等待重连")
        elif op == "online":
            if self.config.role == "server":
                self.link.send({"op":"layout", "side": self.config.side})
            self.notice.emit("文件通道已连接 · 可拖到对面文件夹")

    def edge_tick(self):
        if self.content is not None:
            self.content.poll()
        if self.clipboard is not None:
            self.clipboard.poll()
        if (not self.link or not self.link.connected.is_set() or self.remote or self.local
                or (self.board is not None and not self.on_mac)
                or (getattr(self, 'duplex', False) and (self.on_mac != (sys.platform == 'darwin')))):
            self.edge.hide()
            return
        down = (ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000 if sys.platform == "win32"
                else self.cg.CGEventSourceButtonState(0, 0))
        if self.board is not None and self.on_mac and not down:
            self.gesture.sample(self.board.revision(), down=False)
        if not down:
            self.edge.hide()
            return
        pos = QCursor.pos()
        screen = QApplication.screenAt(pos)
        if not screen:
            return
        rect = screen.geometry()
        left = (self.shared_side == "left") == (self.config.role == "server")
        distance = pos.x() - rect.left() if left else rect.right() - pos.x()
        if distance > 240:
            self.edge.hide()
            return
        x = rect.left() if left else rect.right() - 79
        self.edge.setGeometry(x, rect.top(), 80, rect.height())
        self.edge.show()

    def native_event(self, msg):
        if not self.link or not getattr(self, 'duplex', False):
            return
        op = msg['op']
        if op == 'physical-button' and msg['button'] == 0 and msg['down']:
            if self.board is not None:
                self.gesture.enter(msg.get('revision'))
        elif op == 'crossing':
            self.duplex_drop = None
            if (0 in msg['buttons'] and self.board is not None
                    and self.gesture.take(self.board.revision(), True)):
                try:
                    paths = self.board.files()
                    if paths:
                        self.arm(paths)
                except (ValueError, OSError):
                    self.notice.emit('未取得拖动文件，请重新拖动')
        elif op == 'source-button' and msg['button'] == 0 and not msg['down']:
            if self.local:
                self.link.send({'op':'release','id':self.local})
                self.local = None
        elif op == 'received-input' and msg['kind'] == 'button' and msg['button'] == 0 and not msg['down']:
            if self.remote:
                self.receive_here()
            else:
                pos = QCursor.pos()
                try:
                    if sys.platform == 'win32':
                        import win32api
                        point = win32api.GetCursorPos()
                    else:
                        point = pos.x(),pos.y()
                    folder = folder_at(point)
                except Exception:
                    folder = None
                self.duplex_drop = folder, time.monotonic()

