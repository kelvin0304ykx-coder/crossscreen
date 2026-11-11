from PySide6.QtCore import QObject, Signal
from .engine import Engine
from .native_engine import NativeEngine


class SharedEngine(QObject):
    status = Signal(str, str)
    running_changed = Signal(bool)
    native_event = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.legacy = Engine(self)
        self.native = NativeEngine(self)
        self.active = self.legacy
        for engine in (self.legacy, self.native):
            engine.status.connect(self.status)
            engine.running_changed.connect(self.running_changed)
        self.native.native_event.connect(self.native_event)

    @property
    def running(self):
        return self.active.running

    def start(self, config, root):
        if self.running:
            raise ValueError('请先停止当前共享')
        self.active = self.native if config.input_mode == 'duplex' else self.legacy
        self.active.start(config, root)

    def stop(self):
        return self.active.stop()
