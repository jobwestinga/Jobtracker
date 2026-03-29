"""
Custom stacked-bar graph widget for daily tracked hours.
"""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


class WorkGraphWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []
        self._tokens: dict = {}
        self.setMinimumHeight(360)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_data(self, data: list[dict]) -> None:
        self._data = data
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

            # Total hours label
            p.setPen(QColor(t["TEXT_SECONDARY"]))
            p.setFont(QFont("SF Pro Text", 8, QFont.Medium))
            p.drawText(
                QRectF(x - 14, chart.top() - 20, bar_width + 28, 16),
                Qt.AlignCenter,
                f"{total_seconds / 3600:.1f}h" if total_seconds else "",
            )

            # Day label
            day_text = day_data.get("date", "")
            try:
                day_text = datetime.fromisoformat(day_text).strftime("%m-%d")
            except ValueError:
                pass
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.setFont(QFont("SF Pro Text", 9))
            p.drawText(
                QRectF(x - 12, chart.bottom() + 6, bar_width + 24, 18),
                Qt.AlignCenter,
                day_text,
            )
