"""Use the standard macOS accessibility authorization APIs, without editing TCC."""
import ctypes
import sys


def mac_accessibility_granted() -> bool:
    if sys.platform != "darwin":
        return True
    framework = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    framework.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(framework.AXIsProcessTrusted())


def request_mac_accessibility() -> bool:
    if sys.platform != "darwin":
        return True
    framework = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    key = ctypes.c_void_p.in_dll(framework, "kAXTrustedCheckOptionPrompt").value
    value = ctypes.c_void_p.in_dll(core, "kCFBooleanTrue").value
    keys = (ctypes.c_void_p * 1)(key)
    values = (ctypes.c_void_p * 1)(value)
    core.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    core.CFDictionaryCreate.restype = ctypes.c_void_p
    options = core.CFDictionaryCreate(None, keys, values, 1, None, None)
    if not options:
        return False
    try:
        framework.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        framework.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        return bool(framework.AXIsProcessTrustedWithOptions(options))
    finally:
        core.CFRelease.argtypes = [ctypes.c_void_p]
        core.CFRelease(options)
