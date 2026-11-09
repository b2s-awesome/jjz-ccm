"""Input backends. The engine only talks to InputBackend, never to a library directly.

Keys use pydirectinput names ("w", "1", "space", "shift", "f"...). "mouse1"/"mouse2"
press the left/right button at the current cursor position.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("input")

MOUSE = {"mouse1": "left", "mouse2": "right", "mouse3": "middle"}


class InputBackend:
    def __init__(self, window):
        self.window = window
        self.held: set[str] = set()
        self._lock = threading.Lock()

    # backend primitives -------------------------------------------------
    def _down(self, key: str): raise NotImplementedError
    def _up(self, key: str): raise NotImplementedError
    def _move(self, x: int, y: int): raise NotImplementedError

    # public API ---------------------------------------------------------
    def key_down(self, key: str):
        with self._lock:
            self._down(key)
            self.held.add(key)

    def key_up(self, key: str):
        with self._lock:
            self._up(key)
            self.held.discard(key)

    def tap(self, key: str, hold: float = 0.0, should_stop=None):
        self.key_down(key)
        try:
            end = time.monotonic() + max(hold, 0.03)
            while time.monotonic() < end and not (should_stop and should_stop()):
                time.sleep(0.01)
        finally:
            self.key_up(key)

    def click(self, x_frac: float, y_frac: float, button: str = "mouse1"):
        rect = self.window.rect()
        if rect is None:
            raise RuntimeError("game window not found")
        left, top, w, h = rect
        self._move(int(left + x_frac * w), int(top + y_frac * h))
        time.sleep(0.05)  # Roblox ignores clicks that land in the same frame as the move
        self.tap(button, 0.05)

    def release_all(self):
        """Release every key the macro is holding. Called on stop, pause and errors."""
        for key in list(self.held):
            try:
                self.key_up(key)
            except Exception as e:
                log.error("Failed to release %s: %s", key, e)


class DryRunInput(InputBackend):
    """Logs actions instead of performing them. Default until the user disables dry run."""

    def _down(self, key): log.debug("[dry-run] down %s", key)
    def _up(self, key): log.debug("[dry-run] up %s", key)
    def _move(self, x, y): log.debug("[dry-run] move %d,%d", x, y)

    def tap(self, key, hold=0.0, should_stop=None):
        log.info("[dry-run] press %s%s", key, f" ({hold:.2f}s)" if hold else "")
        super().tap(key, 0.0, should_stop)

    def click(self, x_frac, y_frac, button="mouse1"):
        log.info("[dry-run] click %s at (%.3f, %.3f)", button, x_frac, y_frac)
        super().click(x_frac, y_frac, button)


class DirectInput(InputBackend):
    """Windows SendInput with scan codes (pydirectinput); Roblox ignores virtual-key events."""

    def __init__(self, window):
        super().__init__(window)
        import pydirectinput
        pydirectinput.PAUSE = 0
        pydirectinput.FAILSAFE = False  # we have our own emergency stop
        self.pdi = pydirectinput

    def _down(self, key):
        if key in MOUSE:
            self.pdi.mouseDown(button=MOUSE[key])
        else:
            self.pdi.keyDown(key)

    def _up(self, key):
        if key in MOUSE:
            self.pdi.mouseUp(button=MOUSE[key])
        else:
            self.pdi.keyUp(key)

    def _move(self, x, y):
        self.pdi.moveTo(x, y)


class PynputInput(InputBackend):
    """Cross-platform fallback (non-Windows dev/testing)."""

    def __init__(self, window):
        super().__init__(window)
        from pynput import keyboard, mouse
        self.kb, self.ms = keyboard.Controller(), mouse.Controller()
        self.Key, self.Button = keyboard.Key, mouse.Button

    def _k(self, key):
        return getattr(self.Key, key) if len(key) > 1 and hasattr(self.Key, key) else key

    def _down(self, key):
        if key in MOUSE:
            self.ms.press(getattr(self.Button, MOUSE[key]))
        else:
            self.kb.press(self._k(key))

    def _up(self, key):
        if key in MOUSE:
            self.ms.release(getattr(self.Button, MOUSE[key]))
        else:
            self.kb.release(self._k(key))

    def _move(self, x, y):
        self.ms.position = (x, y)


def make_input(settings, window) -> InputBackend:
    if settings.dry_run:
        return DryRunInput(window)
    try:
        return DirectInput(window)
    except ImportError:
        log.warning("pydirectinput unavailable; using pynput (may not register in Roblox)")
        return PynputInput(window)
