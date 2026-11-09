"""Screen capture of the game window, plus the Vision facade the engine uses."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from vision.detector import Match, TemplateLibrary, bar_fill, crop, match_template

log = logging.getLogger(__name__)


class ScreenSource:
    """Grabs the window's client area with mss (fast, no extra deps on Windows)."""

    def __init__(self, window):
        self.window = window
        self._sct = None

    def grab(self) -> Optional[np.ndarray]:
        rect = self.window.rect()
        if rect is None:
            return None
        if self._sct is None:  # mss handles are per-thread; created lazily on the engine thread
            import mss
            self._sct = mss.mss()
        left, top, w, h = rect
        shot = self._sct.grab({"left": left, "top": top, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR


class Vision:
    def __init__(self, source, templates: TemplateLibrary, reference_height: int,
                 default_threshold: float = 0.8, tesseract_cmd: str = ""):
        self.source = source
        self.templates = templates
        self.reference_height = reference_height
        self.default_threshold = default_threshold
        self.tesseract_cmd = tesseract_cmd
        self.last_frame: Optional[np.ndarray] = None
        self._scale_cache: dict[tuple[str, int], float] = {}

    def frame(self) -> Optional[np.ndarray]:
        self.last_frame = self.source.grab()
        return self.last_frame

    def check(self, det: Optional[dict], frame=None, fmt: Optional[dict] = None) -> Match:
        """Evaluate a template detector. Missing detector or template -> not found."""
        if not det:
            return Match(False, 0.0)
        frame = self.last_frame if frame is None else frame
        if frame is None:
            return Match(False, 0.0)
        name = det["template"].format(**(fmt or {}))
        tpl = self.templates.get(name)
        if tpl is None:
            log.warning("Template %r missing (expected %s)", name, self.templates.path(name))
            return Match(False, 0.0)
        scale = frame.shape[0] / self.reference_height
        key = (name, frame.shape[0])  # a resolution change invalidates the cached scale
        cached = self._scale_cache.get(key)
        m = match_template(frame, tpl, det.get("region"), det.get("threshold", self.default_threshold),
                           scale, det.get("color", True), [cached] if cached else None)
        if m.found:
            self._scale_cache[key] = m.scale
        return m

    def bar(self, det: Optional[dict], frame=None) -> Optional[float]:
        frame = self.last_frame if frame is None else frame
        if not det or frame is None:
            return None
        return bar_fill(frame, det["region"], det["color"], det.get("tolerance", 40))

    def read_number(self, region, frame=None) -> Optional[int]:
        """OCR digits in a region. None when OCR is unavailable or nothing was read."""
        frame = self.last_frame if frame is None else frame
        if frame is None:
            return None
        try:
            import pytesseract
            if self.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
        except ImportError:
            log.warning("pytesseract not installed; OCR disabled")
            return None
        area, _, _ = crop(frame, region)
        gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        try:
            text = pytesseract.image_to_string(bw, config="--psm 7 -c tessedit_char_whitelist=0123456789,.kKmM")
        except Exception as e:  # tesseract binary missing etc.
            log.warning("OCR failed: %s", e)
            return None
        return parse_number(text)

    def save_frame(self, directory: Path, tag: str) -> Optional[Path]:
        if self.last_frame is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
        cv2.imwrite(str(p), self.last_frame)
        return p


def parse_number(text: str) -> Optional[int]:
    """'1,250' -> 1250, '5.6k' -> 5600, '1.4M' -> 1400000."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM]?)", text or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)
    return int(round(val * mult))
