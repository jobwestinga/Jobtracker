"""
Custom stacked-bar graph widget for daily tracked hours.
"""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


class _GraphCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []
        self._tokens: dict = {}
        self._fit_width: bool = True
        self.setMinimumHeight(360)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_data(self, data: list[dict], fit_width: bool) -> None:
        self._data = data
        self._fit_width = fit_width
        
        # If not fitting to width, ensure each bar gets decent space
        if not self._fit_width and self._data:
            # Approx 50px per bar minimum + margins
            min_canvas_width = 80 + len(self._data) * 50
            self.setMinimumWidth(max(400, min_canvas_width))
        else:
            self.setMinimumWidth(100)
            
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        t = self._tokens or {
            "TEXT_PRIMARY": "#E2E8F0",
            "TEXT_SECONDARY": "#94A3B8",
            "TEXT_DIMMED": "#475569",
            "BORDER_COLOR": "#1E3050",
            "BG_SECONDARY": "#131D2E",
        }

        outer = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 160), 1))
        p.setBrush(_with_alpha(t["BG_SECONDARY"], 120))
        p.drawRoundedRect(outer, 12, 12)

        chart = QRectF(44, 26, max(80, self.width() - 66), max(120, self.height() - 70))

        if not self._data:
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.drawText(chart.toRect(), Qt.AlignCenter, "No tracked data yet")
            return

        max_total = max((d.get("total_seconds", 0) for d in self._data), default=0)
        max_total = max(max_total, 3600)
        max_total = int(math.ceil(max_total / 1800) * 1800)

        # Minimal grid: baseline + top line only, to keep the chart less busy.
        p.setFont(QFont("SF Pro Text", 9))
        top_y = chart.top()
        base_y = chart.bottom()

        p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 90), 1))
        p.drawLine(int(chart.left()), int(top_y), int(chart.right()), int(top_y))
        p.drawLine(int(chart.left()), int(base_y), int(chart.right()), int(base_y))

        p.setPen(QColor(t["TEXT_DIMMED"]))
        p.drawText(4, int(top_y) + 4, f"{max_total / 3600:.1f}h")
        p.drawText(4, int(base_y) + 4, "0.0h")

        bar_count = max(1, len(self._data))
        gap = 10
        total_gap = gap * (bar_count - 1)
        bar_width = max(12, (chart.width() - total_gap) / bar_count)

        for idx, day_data in enumerate(self._data):
            x = chart.left() + idx * (bar_width + gap)
            total_seconds = max(0, int(day_data.get("total_seconds", 0)))
            segments = day_data.get("segments", [])

            bar_rect = QRectF(x, chart.top(), bar_width, chart.height())
            clip = QPainterPath()
            clip.addRoundedRect(bar_rect, 7, 7)

            p.save()
            p.setClipPath(clip)
            y_cursor = chart.bottom()
            for seg in segments:
                seconds = max(0, int(seg.get("seconds", 0)))
                if seconds == 0:
                    continue
                height = chart.height() * (seconds / max_total)
                y_cursor -= height

                rect = QRectF(x, y_cursor, bar_width, max(1.0, height))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(seg.get("color", "#3B82F6")))
                p.drawRect(rect)
            p.restore()

            # Outline for each day bar
            p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 170), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(bar_rect, 7, 7)

            # Total hours label with dynamic shiny contour
            if bar_width > 20 and total_seconds > 0:
                total_h = total_seconds / 3600.0
                
                # Determine contour color and font scale
                if total_h <= 1.0:
                    c_hex = "#737373" # grey
                    s = 0.8
                elif total_h <= 2.5:
                    c_hex = "#86EFAC" # green (light)
                    s = 0.95
                elif total_h <= 4.5:
                    c_hex = "#60A5FA" # blue
                    s = 1.0
                elif total_h <= 7.0:
                    c_hex = "#A855F7" # purple
                    s = 1.05
                elif total_h <= 10.0:
                    c_hex = "#FB923C" # orange
                    s = 1.15
                else:
                    c_hex = "#EF4444" # fiery red
                    s = 1.25

                text = f"{total_h:.1f}h"
                base_font_size = 9
                scaled_size = max(7, int(base_font_size * s))
                p.setFont(QFont("SF Pro Text", scaled_size, QFont.Bold))
                
                text_rect = QRectF(x - 24, chart.top() - 25, bar_width + 48, 20)
                
                # Draw the shiny contour (multi-offset outer stroke)
                p.setPen(QPen(_with_alpha(c_hex, 220), 1))
                for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (-1, 0), (1, 0), (0, -1), (0, 1)]:
                    p.drawText(text_rect.translated(ox, oy), Qt.AlignCenter, text)
                
                # Draw the main text overtop
                p.setPen(QColor(t["TEXT_PRIMARY"]))
                p.drawText(text_rect, Qt.AlignCenter, text)

            # Day label
            day_text = day_data.get("date", "")
            try:
                day_text = datetime.fromisoformat(day_text).strftime("%m-%d")
            except ValueError:
                pass
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.setFont(QFont("SF Pro Text", 9))
            # Hide text if width is too small on fit_width mode and bar_count is high
            if bar_width < 16 and self._fit_width and bar_count > 14 and idx % 2 != 0:
                pass # skip drawing every other label if too tight
            else:
                p.drawText(
                    QRectF(x - 12, chart.bottom() + 6, bar_width + 24, 18),
                    Qt.AlignCenter,
                    day_text,
                )


class WorkGraphWidget(QWidget):
    """Scrollable bar chart widget."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._canvas = _GraphCanvas()
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll, 1)
        self.setMinimumHeight(360)

    def set_tokens(self, tokens: dict) -> None:
        self._canvas.set_tokens(tokens)

    def set_data(self, data: list[dict], fit_width: bool = True) -> None:
        self._scroll.setWidgetResizable(fit_width)
        self._canvas.set_data(data, fit_width)
        # Force height constraint
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))
