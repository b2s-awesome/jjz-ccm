"""Drag a rectangle on a screenshot to save a template and get its region fractions."""
from __future__ import annotations

import cv2
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QRubberBand, QVBoxLayout)


def to_pixmap(frame) -> QPixmap:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())


class _Canvas(QLabel):
    def __init__(self, pix: QPixmap, on_select):
        super().__init__()
        self.setPixmap(pix)
        self.setFixedSize(pix.size())
        self.band = QRubberBand(QRubberBand.Rectangle, self)
        self.origin = QPoint()
        self.on_select = on_select

    def mousePressEvent(self, e):
        self.origin = e.position().toPoint()
        self.band.setGeometry(QRect(self.origin, QSize()))
        self.band.show()

    def mouseMoveEvent(self, e):
        self.band.setGeometry(QRect(self.origin, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        self.on_select(self.band.geometry())


class CaptureDialog(QDialog):
    """Returns (name, crop, region_fractions) via .result_data after exec()."""

    def __init__(self, frame, parent=None, max_w=1280):
        super().__init__(parent)
        self.setWindowTitle("Capture template: drag around the UI element")
        self.frame = frame
        h, w = frame.shape[:2]
        self.scale = min(1.0, max_w / w)
        pix = to_pixmap(frame).scaled(int(w * self.scale), int(h * self.scale),
                                      Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.sel = None
        self.result_data = None
        self.info = QLabel("Drag to select.")
        self.name = QLineEdit()
        self.name.setPlaceholderText("template name, e.g. replay_button")
        lay = QVBoxLayout(self)
        lay.addWidget(_Canvas(pix, self._selected))
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        row.addWidget(self.name)
        lay.addLayout(row)
        lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _selected(self, rect: QRect):
        h, w = self.frame.shape[:2]
        s = self.scale
        x0, y0 = int(rect.x() / s), int(rect.y() / s)
        x1, y1 = min(w, int((rect.x() + rect.width()) / s)), min(h, int((rect.y() + rect.height()) / s))
        if x1 - x0 < 6 or y1 - y0 < 6:
            self.info.setText("Selection too small.")
            return
        self.sel = (x0, y0, x1, y1)
        frac = [round(x0 / w, 3), round(y0 / h, 3), round((x1 - x0) / w, 3), round((y1 - y0) / h, 3)]
        self.info.setText(f"{x1 - x0}x{y1 - y0}px  region: {frac}")

    def _accept(self):
        name = self.name.text().strip()
        if not self.sel or not name or not name.replace("_", "").isalnum():
            self.info.setText("Select an area and enter a name (letters, digits, _).")
            return
        x0, y0, x1, y1 = self.sel
        h, w = self.frame.shape[:2]
        frac = [round(x0 / w, 3), round(y0 / h, 3), round((x1 - x0) / w, 3), round((y1 - y0) / h, 3)]
        self.result_data = (name, self.frame[y0:y1, x0:x1].copy(), frac)
        self.accept()
