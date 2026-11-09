"""Template matching and bar reading on BGR frames.

All regions are fractions [x, y, w, h] of the game window, so detection survives
resolution changes. Templates are scaled by current_height / reference_height.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Match:
    found: bool
    confidence: float
    center: Optional[tuple[float, float]] = None  # fractions of the full frame

    def __bool__(self):
        return self.found


def crop(frame: np.ndarray, region) -> tuple[np.ndarray, int, int]:
    h, w = frame.shape[:2]
    if not region:
        return frame, 0, 0
    x, y, rw, rh = region
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = min(w, x0 + max(1, int(rw * w))), min(h, y0 + max(1, int(rh * h)))
    return frame[y0:y1, x0:x1], x0, y0


class TemplateLibrary:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._cache: dict[str, np.ndarray] = {}

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.png"

    def get(self, name: str) -> Optional[np.ndarray]:
        if name not in self._cache:
            p = self.path(name)
            img = cv2.imread(str(p), cv2.IMREAD_COLOR) if p.exists() else None
            if img is None:
                return None
            self._cache[name] = img
        return self._cache[name]

    def reload(self):
        self._cache.clear()


def match_template(frame, template, region=None, threshold=0.8, scale=1.0) -> Match:
    """Best normalized-correlation match of template inside region, tried at a few scales."""
    area, ox, oy = crop(frame, region)
    best = Match(False, 0.0)
    fh, fw = frame.shape[:2]
    for s in (scale, scale * 0.9, scale * 1.1):
        t = template if abs(s - 1.0) < 1e-3 else cv2.resize(
            template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > area.shape[0] or tw > area.shape[1]:
            continue
        res = cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED)
        _, conf, _, loc = cv2.minMaxLoc(res)
        if conf > best.confidence:
            cx, cy = (ox + loc[0] + tw / 2) / fw, (oy + loc[1] + th / 2) / fh
            best = Match(conf >= threshold, float(conf), (cx, cy))
    return best


def bar_fill(frame, region, color_rgb, tolerance=40, column_ratio=0.4) -> Optional[float]:
    """Fraction (0..1) of a horizontal bar filled with color_rgb. None if region empty.

    A column counts as filled when >= column_ratio of its pixels match the colour, which
    tolerates text or borders drawn over the bar.
    """
    area, _, _ = crop(frame, region)
    if area.size == 0:
        return None
    target = np.array(color_rgb[::-1], dtype=np.int16)  # to BGR
    diff = np.abs(area.astype(np.int16) - target).max(axis=2)
    cols = (diff <= tolerance).mean(axis=0) >= column_ratio
    return float(cols.mean())
