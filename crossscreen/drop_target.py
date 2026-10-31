"""Resolve only the real file-manager window under the release point."""
import ctypes
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


def windows_folder(point):
    import pythoncom
    import win32gui
    from win32com.client import Dispatch
    from win32com.shell import shell
    hit = win32gui.WindowFromPoint(point)
    root = win32gui.GetAncestor(hit, 2)
    if win32gui.GetClassName(root) not in ("CabinetWClass", "ExploreWClass"):
        return None
    folders = []
    for window in Dispatch("Shell.Application").Windows():
        if int(window.HWND) != root:
            continue
        provider = window._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
        browser = provider.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
        view = browser.QueryActiveShellView().GetWindow()
        if not win32gui.IsWindowVisible(view) or not (hit == view or win32gui.IsChild(view, hit)):
            continue
        path = Path(window.Document.Folder.Self.Path)
        if path.is_absolute() and path.is_dir():
            folders.append(path)
    if len(set(folders)) == 1:
        return folders[0]


def mac_folder(point):
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    proc = ctypes.CDLL("/usr/lib/libproc.dylib")
    ptr = ctypes.c_void_p
    ax.AXUIElementCreateSystemWide.restype = ptr
    ax.AXUIElementCopyElementAtPosition.argtypes = [ptr, ctypes.c_float, ctypes.c_float, ctypes.POINTER(ptr)]
    ax.AXUIElementCopyAttributeValue.argtypes = [ptr, ptr, ctypes.POINTER(ptr)]
    ax.AXUIElementGetPid.argtypes = [ptr, ctypes.POINTER(ctypes.c_int)]
    core.CFStringCreateWithCString.argtypes = [ptr, ctypes.c_char_p, ctypes.c_uint32]
    core.CFStringCreateWithCString.restype = ptr
    core.CFStringGetCString.argtypes = [ptr, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    core.CFGetTypeID.argtypes = [ptr]
    core.CFGetTypeID.restype = core.CFStringGetTypeID.restype = ctypes.c_ulong
    core.CFRelease.argtypes = [ptr]
    utf8 = 0x08000100
    syswide = ax.AXUIElementCreateSystemWide()
    owned = [syswide]

    def attribute(element, key):
        name = core.CFStringCreateWithCString(None, key, utf8)
        value = ptr()
        try:
            if ax.AXUIElementCopyAttributeValue(element, name, ctypes.byref(value)) == 0:
                owned.append(value)
                return value
        finally:
            core.CFRelease(name)

    try:
        hit = ptr()
        if ax.AXUIElementCopyElementAtPosition(syswide, *point, ctypes.byref(hit)):
            return None
        owned.append(hit)
        pid = ctypes.c_int()
        executable = ctypes.create_string_buffer(4096)
        if ax.AXUIElementGetPid(hit, ctypes.byref(pid)) or not proc.proc_pidpath(pid.value, executable, 4096):
            return None
        if not executable.value.endswith(b"/Finder.app/Contents/MacOS/Finder"):
            return None
        window = attribute(hit, b"AXWindow")
        document = attribute(window or hit, b"AXDocument")
        if not document:
            proxy = attribute(window or hit, b"AXProxy")
            document = attribute(proxy, b"AXURL") if proxy else None
        core.CFURLGetTypeID.restype = ctypes.c_ulong
        if document and core.CFGetTypeID(document) == core.CFURLGetTypeID():
            core.CFURLGetString.argtypes = [ptr]
            core.CFURLGetString.restype = ptr
            document = core.CFURLGetString(document)
        if not document or core.CFGetTypeID(document) != core.CFStringGetTypeID():
            if not window:
                return None
            number = ctypes.c_uint32()
            get_window = getattr(ax, "_AXUIElementGetWindow", None)
            if not get_window:
                return None
            get_window.argtypes = [ptr, ctypes.POINTER(ctypes.c_uint32)]
            if get_window(window, ctypes.byref(number)) or not number.value:
                return None
            # Finder's target is addressed by the actual hit window's unique ID.
            # Window titles can repeat and the front window might be elsewhere.
            script = ('tell application id "com.apple.finder" to get POSIX path of '
                      f'(target of window id {number.value} as alias)')
            result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=25)
            if result.returncode:
                if b"-1743" in result.stderr:
                    raise ValueError("请在系统设置的隐私与安全性 → 自动化中允许跨屏访问 Finder，再拖一次")
                return None
            path = Path(os.fsdecode(result.stdout).removesuffix("\n"))
            return path if path.is_absolute() and path.is_dir() else None
        buf = ctypes.create_string_buffer(16384)
        if core.CFStringGetCString(document, buf, len(buf), utf8):
            url = urlsplit(buf.value.decode())
            if url.scheme == "file" and url.netloc in ("", "localhost"):
                path = Path(unquote(url.path))
                if path.is_absolute() and path.is_dir():
                    return path
    finally:
        for value in reversed(owned):
            core.CFRelease(value)


def folder_at(point):
    return windows_folder(point) if os.name == "nt" else mac_folder(point)

