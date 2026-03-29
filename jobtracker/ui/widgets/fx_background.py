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
        elif self._fx == "Glassmorphism":
            self._paint_glass(p, w, h, t)
        elif self._fx == "Neon":
            self._paint_neon(p, w, h, t)
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

    def _paint_glass(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Frosted prism texture: global patterned overlays + subtle moving light bands.
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]))
        base.setColorAt(0.45, QColor(t["BG_SECONDARY"]))
        base.setColorAt(1.0, QColor(t["BG_TERTIARY"]))
        p.fillRect(self.rect(), base)

        # Full-screen frosted patterns make the entire texture feel distinct.
        p.fillRect(self.rect(), QBrush(_with_alpha(t["TEXT_PRIMARY"], 11), Qt.Dense6Pattern))
        p.fillRect(self.rect(), QBrush(_with_alpha(t["ACCENT"], 7), Qt.DiagCrossPattern))

        # Subtle animated vertical light bands.
        for i in range(4):
            center = w * (0.14 + i * 0.24) + 34 * math.sin(self._phase * (0.8 + i * 0.12))
            band = QLinearGradient(center - 90, 0, center + 90, 0)
            band.setColorAt(0.0, QColor(0, 0, 0, 0))
            band.setColorAt(0.5, _with_alpha(t["TEXT_PRIMARY"], 30 - i * 3))
            band.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), band)

        # Brushed haze from top and bottom.
        haze_top = QLinearGradient(0, 0, 0, h * 0.35)
        haze_top.setColorAt(0.0, _with_alpha(t["TEXT_PRIMARY"], 36))
        haze_top.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, int(h * 0.42), haze_top)

        haze_bottom = QLinearGradient(0, h, 0, h * 0.62)
        haze_bottom.setColorAt(0.0, _with_alpha(t["ACCENT"], 26))
        haze_bottom.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, int(h * 0.56), w, h, haze_bottom)

    def _paint_neon(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Luminous grid: darker base + animated lanes + sweeping beams.
        dark = QColor(t["BG_PRIMARY"])
        dark = dark.darker(185)
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, dark)
        base.setColorAt(0.7, QColor(t["BG_PRIMARY"]))
        base.setColorAt(1.0, QColor(t["BG_SECONDARY"]))
        p.fillRect(self.rect(), base)

        p.setPen(QPen(_with_alpha(t["ACCENT"], 30), 1))
        step = 28
        for x in range(0, w + step, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + step, step):
            p.drawLine(0, y, w, y)

        # Vertical beam lane.
        lane = QLinearGradient(0, 0, w, 0)
        center = 0.5 + 0.20 * math.sin(self._phase * 1.15)
        lane.setColorAt(max(0.0, center - 0.16), QColor(0, 0, 0, 0))
        lane.setColorAt(center, _with_alpha(t["ACCENT"], 96))
        lane.setColorAt(min(1.0, center + 0.16), QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), lane)

        # Diagonal pulse wave.
        wave = QPainterPath()
        slide = 160 * math.cos(self._phase * 0.75)
        wave.moveTo(w * -0.15 + slide, h * 1.05)
        wave.cubicTo(w * 0.18 + slide, h * 0.70, w * 0.35 + slide, h * 0.35, w * 0.62 + slide, -30)
        wave.lineTo(w * 0.72 + slide, -30)
        wave.cubicTo(w * 0.44 + slide, h * 0.34, w * 0.29 + slide, h * 0.68, w * -0.03 + slide, h * 1.05)
        wave.closeSubpath()
        p.fillPath(wave, _with_alpha(t["ACCENT"], 34))

        # Horizontal scanline shimmer.
        scan = QLinearGradient(0, 0, 0, h)
        y_center = 0.22 + 0.6 * (0.5 + 0.5 * math.sin(self._phase * 1.6))
        scan.setColorAt(max(0.0, y_center - 0.06), QColor(0, 0, 0, 0))
        scan.setColorAt(y_center, _with_alpha(t["ACCENT"], 70))
        scan.setColorAt(min(1.0, y_center + 0.06), QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), scan)

        # Vignette edges
        vignette = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.75)
        vignette.setColorAt(0.62, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 130))
        p.fillRect(self.rect(), vignette)
