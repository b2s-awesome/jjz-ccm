"""Locate the Roblox window. Windows uses the Win32 API via ctypes; elsewhere a manual region."""
from __future__ import annotations

import sys
from typing import Optional

Rect = tuple[int, int, int, int]  # left, top, width, height (client area, screen coords)


class ManualWindow:
    """Fixed screen region. Used on non-Windows hosts or when the user opts for manual mode."""

    def __init__(self, region):
        self.region = tuple(int(v) for v in region)

    def rect(self) -> Optional[Rect]:
        return self.region

    def is_foreground(self) -> bool:
        return True

    def focus(self) -> bool:
        return True


class Win32Window:
    def __init__(self, title: str):
        import ctypes
        from ctypes import wintypes

        self.title = title
        self.user32 = ctypes.windll.user32
        self.ctypes, self.wintypes = ctypes, wintypes
        try:  # client coordinates must match mss pixels on high-DPI displays
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    def _hwnd(self):
        hwnd = self.user32.FindWindowW(None, self.title)
        return hwnd or None

    def rect(self) -> Optional[Rect]:
        hwnd = self._hwnd()
        if not hwnd or self.user32.IsIconic(hwnd):
            return None
        r = self.wintypes.RECT()
        self.user32.GetClientRect(hwnd, self.ctypes.byref(r))
        pt = self.wintypes.POINT(0, 0)
        self.user32.ClientToScreen(hwnd, self.ctypes.byref(pt))
        if r.right <= 0 or r.bottom <= 0:
            return None
        return pt.x, pt.y, r.right, r.bottom

    def is_foreground(self) -> bool:
        hwnd = self._hwnd()
        return bool(hwnd) and self.user32.GetForegroundWindow() == hwnd

    def focus(self) -> bool:
        hwnd = self._hwnd()
        if not hwnd:
            return False
        self.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        self.user32.SetForegroundWindow(hwnd)
        return self.is_foreground()


def make_window(settings):
    if settings.use_manual_region or sys.platform != "win32":
        return ManualWindow(settings.manual_region)
    return Win32Window(settings.window_title)
