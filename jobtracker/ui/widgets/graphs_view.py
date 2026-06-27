"""
Custom stacked-bar graph widget for daily tracked hours.
"""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout

from ...core.themes import DEFAULT_TOKENS


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


def _intensity_style(seconds: float) -> tuple[str, float]:
    """Color/scale the total label by average tracked time per day."""
    hours = max(0.0, float(seconds)) / 3600.0
    if hours <= 1.0:
        return "#737373", 0.8
    if hours <= 2.5:
        return "#86EFAC", 0.95
    if hours <= 4.5:
        return "#60A5FA", 1.0
    if hours <= 7.0:
        return "#A855F7", 1.05
    if hours <= 10.0:
        return "#FB923C", 1.15
    return "#EF4444", 1.25


def _bucket_label(date_iso: str, grouping: str) -> str:
    try:
        bucket_date = datetime.fromisoformat(date_iso).date()
    except ValueError:
        return date_iso
    if grouping == "weekly":
        _, iso_week, _ = bucket_date.isocalendar()
        return f"W{iso_week:02d}"
    if grouping == "monthly":
        return bucket_date.strftime("%b %y")
    return bucket_date.strftime("%m-%d")


def _merge_adjacent_segments(segments: list[dict]) -> list[dict]:
    """Collapse consecutive same-subject sessions into one seamless segment."""
    merged: list[dict] = []
    for segment in segments:
        seconds = max(0, int(segment.get("seconds", 0)))
        if seconds == 0:
            continue
        key = (
            segment.get("subject_id"),
            segment.get("subject_name"),
            segment.get("color"),
        )
        if merged and merged[-1]["_merge_key"] == key:
            merged[-1]["seconds"] += seconds
            continue
        item = dict(segment)
        item["seconds"] = seconds
        item["_merge_key"] = key
        merged.append(item)
    return merged


class _GraphCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self._data: list[dict] = []
        self._tokens: dict = {}
        self._fit_width: bool = True
        self._grouping: str = "daily"
        self.setMinimumHeight(360)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_data(
        self,
        data: list[dict],
        fit_width: bool,
        grouping: str = "daily",
    ) -> None:
        self._data = data
        self._fit_width = fit_width
        self._grouping = grouping
        
        bar_count = max(1, len(self._data))
        gap = 10
        min_bar_width = 12
        left_margin = 44
        right_margin = 22
        required_width = left_margin + right_margin + bar_count * min_bar_width + (bar_count - 1) * gap
        
        # If not fitting to width, ensure each bar gets decent space
        if not self._fit_width and self._data:
            # Approx 50px per bar minimum + margins
            min_canvas_width = 80 + len(self._data) * 50
            self.setMinimumWidth(max(max(400, min_canvas_width), required_width))
        else:
            self.setMinimumWidth(100)
            
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        t = self._tokens or DEFAULT_TOKENS

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
        if self._fit_width and bar_count > 15:
            gap = 4
        total_gap = gap * (bar_count - 1)
        bar_width = (chart.width() - total_gap) / bar_count
        if not self._fit_width:
            bar_width = max(12, bar_width)
        else:
            bar_width = max(2, bar_width)

        for idx, day_data in enumerate(self._data):
            x = chart.left() + idx * (bar_width + gap)
            total_seconds = max(0, int(day_data.get("total_seconds", 0)))
            segments = _merge_adjacent_segments(day_data.get("segments", []))

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

                # A small overlap removes antialiasing hairlines between stacked
                # colors while the bar-level clip preserves the outer shape.
                rect = QRectF(
                    x,
                    y_cursor - 0.5,
                    bar_width,
                    max(1.0, height) + 1.0,
                )
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
                intensity_seconds = day_data.get(
                    "intensity_seconds", total_seconds
                )
                c_hex, s = _intensity_style(intensity_seconds)

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
            day_text = _bucket_label(
                day_data.get("date", ""), self._grouping
            )
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
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll, 1)
        self.setMinimumHeight(360)

    def set_tokens(self, tokens: dict) -> None:
        self._canvas.set_tokens(tokens)

    def set_data(
        self,
        data: list[dict],
        fit_width: bool = True,
        grouping: str = "daily",
    ) -> None:
        self._scroll.setWidgetResizable(fit_width)
        self._canvas.set_data(data, fit_width, grouping)
        # Force height constraint
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))
