import pytest


def test_pointer_returns_from_remote_shared_edge_but_not_other_edges():
    from crossscreen.input_geometry import Display
    d = Display(0, 0, 1000, 800)
    assert d.crossed('left', 0, 30, -4, 0)
    assert not d.crossed('left', 0, 30, 4, 0)
    assert not d.crossed('left', 999, 30, 4, 0)
    assert d.entry('left', .5) == (8, 400)
    assert d.entry('right', .5) == (991, 400)
    assert d.ratio(799) == 1


def test_native_windows_input_structures_match_win64_abi():
    import sys
    if sys.platform != 'win32':
        pytest.skip('Windows ABI')
    import ctypes
    from crossscreen.input_windows import INPUT, MOUSEINPUT, KEYBDINPUT, MSLLHOOKSTRUCT, KBDLLHOOKSTRUCT
    assert ctypes.sizeof(INPUT) == 40
    assert ctypes.sizeof(MOUSEINPUT) == 32
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(MSLLHOOKSTRUCT) == 32
    assert ctypes.sizeof(KBDLLHOOKSTRUCT) == 24


def test_native_backend_import_is_lazy_and_does_not_install_hooks():
    from crossscreen.native_engine import make_backend
    backend = make_backend()
    assert not backend.started


def test_return_restores_physically_held_modifiers_on_source():
    from PySide6.QtCore import QCoreApplication
    from crossscreen.native_engine import NativeEngine
    from crossscreen.input_geometry import Display
    app = QCoreApplication.instance() or QCoreApplication([])
    class Backend:
        keys = set()
        hidden = False
        events = []
        def display(self): return Display(0,0,1000,800)
        def warp(self, *point): pass
        def inject(self, event):
            self.keys.add(event['key'])
            self.events.append(event)
    engine = NativeEngine()
    engine.backend = Backend()
    engine.physical_keys = {'shift_l'}
    engine._action({'op':'restore','ratio':.5})
    assert engine.backend.keys == set()  # restored physical key is not remotely owned
    assert engine.backend.events == [{'kind':'key','key':'shift_l','down':True}]


def test_stop_unhooks_and_shows_cursor_even_if_key_release_fails():
    from PySide6.QtCore import QCoreApplication
    from crossscreen.native_engine import NativeEngine
    app = QCoreApplication.instance() or QCoreApplication([])
    class Backend:
        hidden = True
        stopped = False
        def stop(self): self.stopped = True
        def capture_mode(self, flag): self.hidden = flag
    class Router:
        def offline(self): raise OSError('release failed')
    e = NativeEngine()
    b = e.backend = Backend()
    e.router = Router()
    assert e.stop() is False
    assert b.stopped and not b.hidden


def test_disconnect_drops_buffered_key_down_before_it_can_be_injected():
    from PySide6.QtCore import QCoreApplication
    from crossscreen.native_engine import NativeEngine
    from crossscreen.duplex import Router
    from threading import Event
    from types import SimpleNamespace
    app = QCoreApplication.instance() or QCoreApplication([])
    e = NativeEngine()
    actions = []
    e.router = Router(False, lambda _:None, actions.append)
    e.router.online()
    e.router.receive({'op':'lease','epoch':1,'owner':'server','ratio':.5,'held':[]})
    e.link = SimpleNamespace(connected=Event(), generation=1)
    e.events.put({'op':'input','_generation':1,'epoch':1,'event':{'kind':'key','key':'a','down':True}})
    e._tick()
    assert not any(a['op'] == 'inject' for a in actions)
    assert e.router.mode == 'local'
