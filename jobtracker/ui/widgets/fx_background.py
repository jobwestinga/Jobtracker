"""
Animated global background layer for Theme FX presets.

Each FX paints a different full-screen background treatment.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from .paint_utils import with_alpha as _with_alpha


class FxBackgroundWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: dict = {}
        self._fx = "Glow"
        self._phase = 0.0
        self._animation_requested = True

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def apply_theme(self, tokens: dict, fx_name: str) -> None:
        self._tokens = tokens
        self._fx = fx_name
        self._sync_timer()
        self.update()

    def set_animating(self, on: bool) -> None:
        """Start/stop the animation loop. Paused when the app is unfocused or
        minimized to avoid burning CPU/battery on an off-screen window."""
        self._animation_requested = on
        self._sync_timer()

    def _sync_timer(self) -> None:
        animated = self._animation_requested and self._fx in {"Glow", "Space"}
        if animated:
            if not self._timer.isActive():
                self._timer.start(40)
        else:
            self._timer.stop()

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

        if self._fx == "Base":
            p.fillRect(self.rect(), QColor(t["BG_PRIMARY"]))
        elif self._fx == "Clean":
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
        # Refined monochrome elevation with a subtle top-edge ambient glow.
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]))
        base.setColorAt(1.0, QColor(t["BG_SECONDARY"]).darker(115))
        p.fillRect(self.rect(), base)

        cx1 = w * (0.52 + 0.04 * math.sin(self._phase * 0.6))
        cy1 = h * (0.08 + 0.02 * math.cos(self._phase * 0.5))
        g1 = QRadialGradient(cx1, cy1, max(w, h) * 0.55)
        g1.setColorAt(0.0, _with_alpha(t["ACCENT"], 26))
        g1.setColorAt(0.5, _with_alpha(t["ACCENT"], 9))
        g1.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g1)

    def _paint_space(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Deep OLED midnight canvas with a calm upper horizon aura.
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, QColor(t["BG_PRIMARY"]).darker(135))
        base.setColorAt(1.0, QColor(t["BG_PRIMARY"]))
        p.fillRect(self.rect(), base)

        cx = w * (0.5 + 0.05 * math.sin(self._phase * 0.4))
        cy = h * (0.18 + 0.03 * math.cos(self._phase * 0.35))
        g1 = QRadialGradient(cx, cy, max(w, h) * 0.62)
        g1.setColorAt(0.0, _with_alpha(t["ACCENT"], 22))
        g1.setColorAt(0.55, _with_alpha(t["ACCENT"], 6))
        g1.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g1)

    def _paint_checkerboard(self, p: QPainter, w: int, h: int, t: dict) -> None:
        # Matte monochrome surface (Minimal preset).
        p.fillRect(self.rect(), QColor(t["BG_PRIMARY"]))
        top_sheen = QLinearGradient(0, 0, 0, min(140, h))
        top_sheen.setColorAt(0.0, _with_alpha(t["TEXT_PRIMARY"], 6))
        top_sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, min(140, h), top_sheen)
