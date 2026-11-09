"""Global hotkeys (work while Roblox has focus) for emergency stop and pause."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Hotkeys:
    def __init__(self, bindings: dict[str, callable]):
        """bindings: {"f8": fn, ...} using pynput key names."""
        self.bindings = {k.lower(): fn for k, fn in bindings.items() if k}
        self.listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as e:  # ImportError, or no display on Linux
            log.error("Global hotkeys unavailable (%s). Use the GUI Stop button.", e)
            return False

        def on_press(key):
            name = getattr(key, "name", None) or getattr(key, "char", None) or ""
            fn = self.bindings.get(str(name).lower())
            if fn:
                try:
                    fn()
                except Exception:
                    log.exception("hotkey handler failed")

        try:
            self.listener = keyboard.Listener(on_press=on_press, daemon=True)
            self.listener.start()
        except Exception as e:
            log.error("Global hotkeys unavailable (%s). Use the GUI Stop button.", e)
            return False
        return True

    def stop(self):
        if self.listener:
            self.listener.stop()
