"""Win32 physical input capture and explicitly tagged native injection."""
import ctypes as C
from ctypes import wintypes as W

from .input_geometry import Display
from .input_keys import WINDOWS, WIN_NAMES

MARKER = 0x4353524E
ULONG_PTR = C.c_size_t
LRESULT = C.c_ssize_t


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class _InputUnion(C.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', _InputUnion)]


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [('pt', W.POINT), ('mouseData', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [('vkCode', W.DWORD), ('scanCode', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class WindowsInput:
    def __init__(self):
        self.started = False
        self.hidden = False
        self.keys = set()
        self.buttons = set()
        self.hooks = []
        self.user = C.WinDLL('user32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.proc_type = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)
        self.user.SetWindowsHookExW.argtypes = [C.c_int, self.proc_type, W.HINSTANCE, W.DWORD]
        self.user.SetWindowsHookExW.restype = W.HHOOK
        self.user.CallNextHookEx.argtypes = [W.HHOOK, C.c_int, W.WPARAM, W.LPARAM]
        self.user.CallNextHookEx.restype = LRESULT
        self.user.UnhookWindowsHookEx.argtypes = [W.HHOOK]
        self.user.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        self.user.SendInput.restype = W.UINT
        self.user.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
        self.kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = W.HMODULE

    def display(self):
        return Display(self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77),
                       self.user.GetSystemMetrics(78), self.user.GetSystemMetrics(79))

    def position(self):
        point = W.POINT()
        if not self.user.GetCursorPos(C.byref(point)):
            raise OSError('无法读取指针位置')
        return point.x, point.y

    def start(self, capture, fault):
        self.capture, self.fault = capture, fault
        self.last_point = self.position()
        self.mouse_proc = self.proc_type(self._mouse)
        self.key_proc = self.proc_type(self._key)
        module = self.kernel.GetModuleHandleW(None)
        try:
            for kind, callback in ((14, self.mouse_proc), (13, self.key_proc)):
                hook = self.user.SetWindowsHookExW(kind, callback, module, 0)
                if not hook:
                    raise OSError('无法启用本机键鼠捕获')
                self.hooks.append(hook)
            self.started = True
        except BaseException:
            self.stop()
            raise

    def stop(self):
        for hook in self.hooks:
            self.user.UnhookWindowsHookEx(hook)
        self.hooks.clear()
        self.started = False
        try:
            self.release()
        finally:
            self.capture_mode(False)

    def capture_mode(self, enabled):
        if bool(enabled) != self.hidden:
            self.user.ShowCursor(not enabled)
            self.hidden = bool(enabled)

    def _next(self, code, kind, pointer):
        return self.user.CallNextHookEx(None, code, kind, pointer)

    def _key(self, code, kind, pointer):
        if code >= 0:
            data = KBDLLHOOKSTRUCT.from_address(pointer)
            if not data.flags & 0x10:
                key = WIN_NAMES.get(data.scanCode | (0x100 if data.flags & 1 else 0))
                try:
                    event = {'kind':'key','key':key,'down':not bool(data.flags & 0x80)}
                    if self.capture(event, self.position()):
                        return 1
                except Exception:
                    self.fault()
        return self._next(code, kind, pointer)

    def _mouse(self, code, kind, pointer):
        if code >= 0:
            data = MSLLHOOKSTRUCT.from_address(pointer)
            if data.flags & 1:
                if kind == 0x200:
                    self.last_point = (data.pt.x, data.pt.y)
            else:
                point = (data.pt.x, data.pt.y)
                event = None
                try:
                    if kind == 0x200:
                        x, y = self.last_point
                        event = {'kind':'move','dx':point[0]-x,'dy':point[1]-y}
                    elif kind in (0x201,0x202,0x204,0x205,0x207,0x208,0x20b,0x20c):
                        button = {0x201:0,0x202:0,0x204:1,0x205:1,0x207:2,0x208:2}.get(kind)
                        if button is None:
                            button = 3 if data.mouseData >> 16 == 1 else 4
                        event = {'kind':'button','button':button,'down':kind in (0x201,0x204,0x207,0x20b)}
                    elif kind in (0x20a,0x20e):
                        delta = C.c_short(data.mouseData >> 16).value
                        # Unified scroll units are pixels; 120 wheel units = 40 px.
                        event = {'kind':'scroll','x':delta/3 if kind == 0x20e else 0,
                                 'y':delta/3 if kind == 0x20a else 0}
                    if event and self.capture(event, point):
                        return 1
                    if kind == 0x200:
                        self.last_point = point
                except Exception:
                    self.fault()
        return self._next(code, kind, pointer)

    def _send(self, item):
        if self.user.SendInput(1, C.byref(item), C.sizeof(INPUT)) != 1:
            raise OSError('Windows 拒绝模拟输入，请回到普通权限的桌面窗口')

    def warp(self, x, y):
        screen = self.display()
        x, y = screen.clamp(x, y)
        self._send(INPUT(type=0, mi=MOUSEINPUT(round((x-screen.x)*65535/max(1,screen.width-1)),
                     round((y-screen.y)*65535/max(1,screen.height-1)), 0, 0xC001, 0, MARKER)))

    def inject(self, event):
        kind = event['kind']
        if kind == 'key':
            key = event['key']
            scan = WINDOWS[key]
            down = event['down']
            self._send(INPUT(type=1, ki=KEYBDINPUT(0, scan & 0xff,
                        8 | (1 if scan & 0x100 else 0) | (0 if down else 2), 0, MARKER)))
            (self.keys.add if down else self.keys.discard)(key)
        elif kind == 'button':
            button, down = event['button'], event['down']
            flags = ((2,4),(8,16),(32,64),(128,256),(128,256))[button][not down]
            self._send(INPUT(type=0, mi=MOUSEINPUT(0,0,button-2 if button >= 3 else 0,flags,0,MARKER)))
            (self.buttons.add if down else self.buttons.discard)(button)
        elif kind == 'scroll':
            for axis, flag in (('y',0x800),('x',0x1000)):
                value = round(event[axis]*3)
                if value:
                    self._send(INPUT(type=0, mi=MOUSEINPUT(0,0,value & 0xffffffff,flag,0,MARKER)))
        elif kind == 'move':
            x,y = self.position()
            self.warp(x+event['dx'], y+event['dy'])

    def release(self):
        try:
            for key in list(self.keys):
                self.inject({'kind':'key','key':key,'down':False})
            for button in list(self.buttons):
                self.inject({'kind':'button','button':button,'down':False})
        finally:
            self.keys.clear()
            self.buttons.clear()
