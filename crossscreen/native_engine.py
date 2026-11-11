"""Qt lifecycle for CrossScreen's own duplex input engine."""
import queue
import sys
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from .duplex import Router, valid_event
from .engine import ensure_engine_not_running
from .input_link import InputLink
from .input_keys import emergency, MODIFIERS
from .security import ensure_identity


def make_backend():
    if sys.platform == 'win32':
        from .input_windows import WindowsInput
        return WindowsInput()
    if sys.platform == 'darwin':
        from .input_macos import MacInput
        return MacInput()
    raise OSError('双向输入暂支持 Windows 与 macOS')


class NativeEngine(QObject):
    status = Signal(str, str)
    running_changed = Signal(bool)
    native_event = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend = self.link = self.router = None
        self.timer = QTimer(self)
        self.timer.setInterval(4)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.physical_keys = set()
        self.physical_buttons = set()
        self.events = queue.Queue(512)
        self.edge = 'left'
        self.cooldown = 0
        self.last_mode = 'local'
        self.generation = 0

    @property
    def running(self):
        return self.backend is not None and self.backend.started

    def start(self, config, root):
        if self.running:
            raise ValueError('请先停止当前共享')
        config.validate()
        if not config.peer_fingerprint:
            raise ValueError('请先导入另一台电脑的配对信息')
        ensure_engine_not_running()
        ensure_identity(root)
        self.config = config
        self.events = queue.Queue(512)
        self.physical_keys.clear()
        self.physical_buttons.clear()
        self.backend = make_backend()
        self.drag_board = None
        if sys.platform == 'darwin':
            from .finder_drag import FinderDragBoard
            self.drag_board = FinderDragBoard()
        self.screen = self.backend.display()
        self.point = self.backend.position()
        self.link = InputLink(config, root / 'tls/identity.pem', self.events.put_nowait)
        self.router = Router(config.role == 'server', self.link.send, self._action)
        self._edge(config.side)
        try:
            self.backend.start(self._capture, self._fault)
            self.link.start()
            self.timer.start()
        except Exception:
            self.stop()
            raise
        self.running_changed.emit(True)
        self.status.emit('waiting','双向键鼠已就绪，等待另一台开启双向模式')

    def _edge(self, side):
        self.edge = side if self.config.role == 'server' else ('right' if side == 'left' else 'left')

    def stop(self):
        self.timer.stop()
        if self.link:
            self.link.stop()
        failed = False
        try:
            if self.router:
                self.router.offline()
        except OSError:
            failed = True
        finally:
            if self.backend:
                try:
                    self.backend.stop()
                except OSError:
                    failed = True
                finally:
                    self.backend.capture_mode(False)
        self.running_changed.emit(False)
        if failed:
            self.status.emit('error','系统未确认按键释放，请在本机按一下修饰键后松开')
            return False
        self.link = self.router = self.backend = None
        self.running_changed.emit(False)
        self.status.emit('stopped','共享已停止，双方键鼠恢复本机')
        return True

    def _fault(self):
        # Native callbacks must always fail open. Never perform network I/O here.
        if self.link:
            self.link.disconnect()
        try:
            if self.router:
                self.router.offline()
        except OSError:
            pass
        finally:
            if self.backend:
                self.backend.capture_mode(False)
        self.cooldown = time.monotonic() + .5

    def _tick(self):
        if not self.router:
            return
        try:
            if not self.link.connected.is_set() and self.router.connected:
                self.router.offline()
            for _ in range(256):
                try:
                    msg = self.events.get_nowait()
                except queue.Empty:
                    break
                if msg.get('_generation') != self.link.generation:
                    continue
                if msg['op'] != 'offline' and not self.link.connected.is_set():
                    continue
                if msg['op'] == 'online':
                    self.router.online()
                    self.generation = self.link.generation
                    self._edge(msg['side'])
                    self.status.emit('connected','两端已连接 · 两套键鼠均可自然跨屏')
                elif msg['op'] == 'offline':
                    self.router.offline()
                    self.status.emit('waiting','双向连接已断开，键鼠恢复本机，正在重连')
                else:
                    self.router.receive(msg)
            self.router.tick()
            if not self.link.connected.is_set() and self.router.connected:
                self.router.offline()
        except (ValueError, OSError, RuntimeError):
            self._fault()
            self.status.emit('waiting','输入通道已恢复本机，正在重新连接')
        mode = self.router.mode
        if mode != self.last_mode:
            self.last_mode = mode
            if self.router.connected and mode in ('send','receive','local'):
                is_mac = (sys.platform == 'darwin') != (mode == 'send')
                self.status.emit('mac' if is_mac else 'windows',
                                 ('正在控制 Mac' if is_mac else '正在控制 Windows') + ' · 双向自然跨屏')

    def _capture(self, event, point):
        router = self.router
        if router is None:
            return False
        if router.connected and (not self.link.connected.is_set() or self.generation != self.link.generation):
            router.offline()
        kind = event['kind']
        if kind == 'key':
            key = event.get('key')
            if key is None:
                return router.mode in ('send','pending')
            (self.physical_keys.add if event['down'] else self.physical_keys.discard)(key)
            if emergency(self.physical_keys):
                self._fault()
                return True
        elif kind == 'button':
            (self.physical_buttons.add if event['down'] else self.physical_buttons.discard)(event['button'])
            revision = self.drag_board.revision() if self.drag_board is not None and event['down'] else None
            self.native_event.emit({'op':'physical-button', 'revision':revision, **event})
        if not router.connected:
            return False
        if router.mode == 'receive':
            # Physical local input takes control. Tagged injected input never gets here.
            router.take_local()
            self.cooldown = time.monotonic() + .15
        if kind == 'move' and router.mode == 'local' and time.monotonic() >= self.cooldown:
            if self.screen.crossed(self.edge,*point,event['dx'],event['dy']):
                # Do not carry mouse-down into another OS's unrelated UI.
                # File drag/drop uses the authenticated file bridge instead.
                self.native_event.emit({'op':'crossing','point':point,'buttons':sorted(self.physical_buttons)})
                router.cross(self.screen.ratio(point[1]), sorted(self.physical_keys))
        if router.mode in ('send','pending'):
            if kind == 'button':
                self.native_event.emit({'op':'source-button', **event})
            if valid_event(event):
                return router.input(event)
            return True
        return False

    def _action(self, action):
        backend = self.backend
        if backend is None:
            return
        op = action['op']
        if op == 'release':
            was_hidden = backend.hidden
            try:
                backend.release()
            finally:
                backend.capture_mode(False)
            if was_hidden and self.router is not None and self.router.mode == 'local':
                self._restore_modifiers()
        elif op == 'capture':
            backend.capture_mode(True)
        elif op == 'source_release':
            for key in action['held']:
                backend.inject({'kind':'key','key':key,'down':False})
            buttons = set(self.physical_buttons)
            if sys.platform == 'darwin' and 0 in buttons:
                from .finder_drag import FinderInput, finish_handoff
                from .input_macos import MARKER
                device = FinderInput(MARKER)
                if device.signature() is not None:
                    router, epoch = self.router, self.router.epoch
                    finish_handoff(device, lambda f: QTimer.singleShot(120, f),
                                   lambda: self.router is router and router.epoch == epoch and router.mode == 'send')
                    buttons.remove(0)
            for button in buttons:
                backend.inject({'kind':'button','button':button,'down':False})
        elif op in ('enter','restore'):
            self.screen = backend.display()
            self.point = self.screen.entry(self.edge, action['ratio'])
            backend.warp(*self.point)
            self.cooldown = time.monotonic() + .12
            for key in action.get('held', []):
                backend.inject({'kind':'key','key':key,'down':True})
            if op == 'restore':
                self._restore_modifiers()
        elif op == 'inject':
            event = action['event']
            if event['kind'] == 'move':
                x,y = self.point
                target = (x+event['dx'],y+event['dy'])
                if self.screen.crossed(self.edge,*target,event['dx'],event['dy']) and time.monotonic() >= self.cooldown:
                    self.router.return_home(self.screen.ratio(y))
                    return
                self.point = self.screen.clamp(*target)
                backend.warp(*self.point)
            else:
                backend.inject(event)
            self.native_event.emit({'op':'received-input', **event})

    def _restore_modifiers(self):
        for key in self.physical_keys & MODIFIERS:
            self.backend.inject({'kind':'key','key':key,'down':True})
            # This key remains physically held; its hardware key-up will follow.
            self.backend.keys.discard(key)
