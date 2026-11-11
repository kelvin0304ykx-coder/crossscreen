"""Explicit interactive QA. Stop other sharing engines before running.

Injects only aB and a click into its own focused test window, then restores
the pointer. Reports booleans only; never captures or saves user text.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget, QVBoxLayout
from crossscreen.native_engine import make_backend
from crossscreen.engine import ensure_engine_not_running

ensure_engine_not_running()
app = QApplication([])
window = QWidget()
window.setWindowTitle('跨屏原生输入验收')
window.resize(500,100)
layout = QVBoxLayout(window)
line = QLineEdit(window)
line.setAttribute(Qt.WA_InputMethodEnabled, False)
layout.addWidget(line)
line.setPlaceholderText('自动验证键盘与点击，约两秒后关闭')
window.show()
window.raise_()
window.activateWindow()
line.setFocus()
if sys.platform == 'darwin':
    from AppKit import NSApplication
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
backend = make_backend()
original = backend.position()
report = {'hook':False,'typed':False,'clicked':False,'loop_free':False,'released':False}
captured = []
deadline = time.monotonic() + 45
finished = False


class Watch(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            report['clicked'] = True
        if event.type() == QEvent.KeyPress:
            report['key_press_count'] = report.get('key_press_count',0) + 1
        return False


watch = Watch()
line.installEventFilter(watch)


def finish():
    global finished
    if finished:
        return
    finished = True
    try:
        value = line.text()
        report['typed'] = value.casefold() == 'ab' and value[0].isupper() != value[1].isupper()
        report['loop_free'] = not captured
    finally:
        backend.stop()
        backend.warp(*original)
        report['released'] = not backend.keys and not backend.buttons and not backend.hidden and not backend.started
        print(json.dumps(report),flush=True)
        window.close()
        app.quit()


def run():
    try:
        if sys.platform == 'win32':
            import ctypes as c
            from ctypes import wintypes as w
            backend.user.GetForegroundWindow.restype = w.HWND
            if backend.user.GetForegroundWindow() != int(window.winId()):
                if '--wait-for-focus' in sys.argv and time.monotonic() < deadline:
                    QTimer.singleShot(200,run)
                    return
                raise RuntimeError('系统前台不属于验收窗口，未注入输入')
        line.setFocus()
        if not window.isActiveWindow() or not line.hasFocus():
            raise RuntimeError('验收窗口未获得焦点')
        if sys.platform == 'win32':
            # Disable composition only for this disposable Latin probe window.
            # The user's global layout and all other windows remain untouched.
            imm = c.WinDLL('imm32')
            imm.ImmAssociateContextEx.argtypes = [w.HWND,w.HANDLE,w.DWORD]
            imm.ImmAssociateContextEx(int(window.winId()),None,0)
            imm.ImmAssociateContextEx(int(window.winId()),None,1)
        if '--no-hooks' not in sys.argv:
            backend.start(lambda e,p: captured.append(e['kind']) or False, lambda:report.update(fault=True))
            report['hook'] = backend.started
        point = line.mapToGlobal(line.rect().center())
        target = point.x(),point.y()
        if sys.platform == 'win32':
            import ctypes as c
            from ctypes import wintypes as w
            rect, pos = w.RECT(), w.POINT()
            backend.user.GetClientRect.argtypes = [w.HWND,c.POINTER(w.RECT)]
            backend.user.ClientToScreen.argtypes = [w.HWND,c.POINTER(w.POINT)]
            backend.user.GetClientRect(int(window.winId()), c.byref(rect))
            pos.x, pos.y = (rect.right-rect.left)//2, (rect.bottom-rect.top)//2
            backend.user.ClientToScreen(int(window.winId()),c.byref(pos))
            target = pos.x,pos.y
        backend.warp(*target)
        backend.inject({'kind':'button','button':0,'down':True})
        backend.inject({'kind':'button','button':0,'down':False})
        for key, down in [('a',True),('a',False),('shift_l',True),('b',True),('b',False),('shift_l',False)]:
            backend.inject({'kind':'key','key':key,'down':down})
    except Exception as exc:
        report['error'] = type(exc).__name__
    QTimer.singleShot(700,finish)


QTimer.singleShot(700,run)
QTimer.singleShot(46000 if '--wait-for-focus' in sys.argv else 5000,finish)
app.exec()
raise SystemExit(0 if all(report.get(k) is True for k in ('hook','typed','clicked','loop_free','released')) else 1)
