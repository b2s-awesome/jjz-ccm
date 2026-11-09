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
    scale: float = 1.0                             # template scale that produced this match

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


COARSE = 0.5          # search resolution factor for the first pass
MIN_COARSE_SIDE = 16  # templates smaller than this (px) after coarsening are matched at full res
COARSE_PEAKS = 3      # distinct coarse candidates re-scored at full res (guards against look-alikes)
MIN_PEAK_CONF = 0.5   # extra candidates below this coarse score are not worth re-scoring


def _resize(img, f):
    if abs(f - 1.0) < 1e-3:
        return img
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)


def _best(area, tpl):
    _, conf, _, loc = cv2.minMaxLoc(cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED))
    return float(conf), loc


def _coarse_peaks(area, tpl, k=COARSE_PEAKS):
    """Top-k distinct peaks of the coarse match map. Each peak's neighbourhood (one template
    size) is suppressed so the k candidates are k different places, not one blob."""
    res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tpl.shape[:2]
    peaks = []
    for _ in range(k):
        _, conf, _, (x, y) = cv2.minMaxLoc(res)
        if peaks and conf < MIN_PEAK_CONF:
            break
        peaks.append((x, y))
        res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return peaks


def match_template(frame, template, region=None, threshold=0.8, scale=1.0,
                   color=True, scales=None) -> Match:
    """Best normalized-correlation match of template inside region.

    color=False matches in grayscale: ~5x faster, but elements that differ mainly by hue
    (e.g. a HUD vs a reward banner of the same shape) can be confused, so it is opt-in. The search
    runs at half resolution, then the top few distinct hits are re-scored at full resolution, so the
    returned confidence and position are full-resolution values. scales defaults to
    scale, ±10%; Vision passes the single cached scale once a template has matched.
    """
    area, ox, oy = crop(frame, region)
    if not color:
        area = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
        template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    ah, aw = area.shape[:2]
    fh, fw = frame.shape[:2]
    best = Match(False, 0.0)
    for s in scales or (scale, scale * 0.9, scale * 1.1):
        t = _resize(template, s)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > ah or tw > aw:
            continue
        if min(th, tw) * COARSE >= MIN_COARSE_SIDE:
            conf, loc = -1.0, (0, 0)
            for cx, cy in _coarse_peaks(_resize(area, COARSE), _resize(t, COARSE)):
                x, y, m = int(cx / COARSE), int(cy / COARSE), int(2 / COARSE) + 2
                x0, y0 = max(0, min(x - m, aw - tw)), max(0, min(y - m, ah - th))
                x1, y1 = min(aw, max(x0 + tw, x + tw + m)), min(ah, max(y0 + th, y + th + m))
                c, (lx, ly) = _best(area[y0:y1, x0:x1], t)
                if c > conf:
                    conf, loc = c, (x0 + lx, y0 + ly)
        else:
            conf, loc = _best(area, t)
        if conf > best.confidence:
            center = ((ox + loc[0] + tw / 2) / fw, (oy + loc[1] + th / 2) / fh)
            best = Match(conf >= threshold, conf, center, s)
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
