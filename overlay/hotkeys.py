"""Global hotkeys.

Windows: RegisterHotKey via ctypes -- fires even while PoE has focus.
Elsewhere: falls back to window-local shortcuts (overlay must be focused).
"""
import sys

VK = {f"F{i}": 0x6F + i for i in range(1, 13)}  # F1=0x70 ... F12=0x7B
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000


def install(app, window, bindings):
    """bindings: {'F3': callable, ...}. Returns True if global keys active."""
    if sys.platform != "win32":
        _fallback(window, bindings)
        return False

    import ctypes
    from ctypes import wintypes
    from PyQt6.QtCore import QAbstractNativeEventFilter

    user32 = ctypes.windll.user32
    actions = {}
    for hk_id, (key, fn) in enumerate(bindings.items(), start=1):
        vk = VK.get(key.upper())
        if vk and user32.RegisterHotKey(None, hk_id, MOD_NOREPEAT, vk):
            actions[hk_id] = fn
        else:
            print(f"[hotkeys] could not register {key} "
                  "(taken by another app? change it in config.json)")

    class _Filter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, etype, message):
            if etype == b"windows_generic_MSG":
                msg = ctypes.cast(int(message),
                                  ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY:
                    fn = actions.get(msg.wParam)
                    if fn:
                        fn()
            return False, 0

    flt = _Filter()
    app.installNativeEventFilter(flt)
    app._hotkey_filter = flt          # keep a reference so it isn't GC'd
    return True


def _fallback(window, bindings):
    from PyQt6.QtGui import QKeySequence, QShortcut
    for key, fn in bindings.items():
        QShortcut(QKeySequence(key), window, activated=fn)
    print("[hotkeys] non-Windows platform: shortcuts work only while the "
          "overlay window is focused")
