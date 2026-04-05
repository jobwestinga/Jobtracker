"""
Animated global background layer for Theme FX presets.

Each FX paints a different full-screen background treatment.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QBrush, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


class FxBackgroundWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: dict = {}
        self._fx = "Glow"
        self._phase = 0.0

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def apply_theme(self, tokens: dict, fx_name: str) -> None:
        self._tokens = tokens
        self._fx = fx_name
        self.update()

    def _tick(self) -> None:
        self._phase += 0.010
        if self._phase > math.tau:
            self._phase -= math.tau
        self.update()

    def paintEvent(self, event) -> None:
        if not self._tokens:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w = max(1, self.width())
        h = max(1, self.height())
        t = self._tokens

        if self._fx == "Clean":
            self._paint_clean(p, w, h, t)
        elif self._fx == "Space":
            self._paint_space(p, w, h, t)
        elif self._fx == "Checkerboard":
            self._paint_checkerboard(p, w, h, t)
        else:
            self._paint_glow(p, w, h, t)

    def _paint_clean(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Flat, minimal gradient with very subtle split tone.
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]))
        base.setColorAt(1.0, QColor(t["BG_SECONDARY"]))
        p.fillRect(self.rect(), base)

        side = QLinearGradient(0, 0, w, 0)
        side.setColorAt(0.0, _with_alpha(t["TEXT_PRIMARY"], 6))
        side.setColorAt(0.5, QColor(0, 0, 0, 0))
        side.setColorAt(1.0, _with_alpha(t["TEXT_PRIMARY"], 4))
        p.fillRect(self.rect(), side)

    def _paint_glow(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Rich atmosphere: full gradient base + drifting soft glows.
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]))
        base.setColorAt(0.35, QColor(t["BG_SECONDARY"]))
        base.setColorAt(1.0, QColor(t["BG_TERTIARY"]))
        p.fillRect(self.rect(), base)

        cx1 = w * (0.22 + 0.08 * math.sin(self._phase))
        cy1 = h * (0.24 + 0.06 * math.cos(self._phase * 0.8))
        g1 = QRadialGradient(cx1, cy1, min(w, h) * 0.58)
        g1.setColorAt(0.0, _with_alpha(t["ACCENT"], 90))
        g1.setColorAt(0.45, _with_alpha(t["ACCENT"], 34))
        g1.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g1)

        cx2 = w * (0.78 + 0.07 * math.cos(self._phase * 1.1))
        cy2 = h * (0.78 + 0.05 * math.sin(self._phase * 1.2))
        g2 = QRadialGradient(cx2, cy2, min(w, h) * 0.45)
        g2.setColorAt(0.0, _with_alpha(t["ACCENT_GREEN"], 60))
        g2.setColorAt(0.5, _with_alpha(t["ACCENT_GREEN"], 20))
        g2.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g2)

    def _paint_space(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Deep space background.
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]).darker(150))
        base.setColorAt(1.0, QColor(t["BG_SECONDARY"]).darker(120))
        p.fillRect(self.rect(), base)

        # Pseudo-random stars
        p.setPen(Qt.NoPen)
        for i in range(150):
            x_hash = (i * 12345) % w
            y_hash = (i * 54321) % h
            size = (i * 13) % 3 + 1
            
            twinkle = (math.sin(self._phase * 2.0 + i) + 1.0) / 2.0
            alpha = int(40 + 160 * twinkle)
            p.setBrush(_with_alpha(t["TEXT_PRIMARY"], alpha))
            p.drawEllipse(int(x_hash), int(y_hash), size, size)

        # Cool light/nebula effects
        cx = w * (0.5 + 0.3 * math.sin(self._phase * 0.5))
        cy = h * (0.5 + 0.3 * math.cos(self._phase * 0.4))
        g1 = QRadialGradient(cx, cy, max(w, h) * 0.7)
        g1.setColorAt(0.0, _with_alpha(t["ACCENT"], 40))
        g1.setColorAt(0.5, _with_alpha(t["ACCENT_GREEN"], 15))
        g1.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g1)

    def _paint_checkerboard(self, p: QPainter, w: int, h: int, t: dict) -> None:
        p.fillRect(self.rect(), QColor(t["BG_PRIMARY"]))
        
        square_size = 24
        p.setPen(Qt.NoPen)
        p.setBrush(_with_alpha(t["BORDER_COLOR"], 60))
        
        cols = w // square_size + 2
        rows = h // square_size + 2
        
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 2 == 0:
                    p.drawRect(col * square_size, row * square_size, square_size, square_size)
