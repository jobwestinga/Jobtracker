"""Native weight chart presentation; pure fitting/axes live in core.weight_plot.

WeightChart remains importable from weight_view for compatibility.
See docs/features/weight.md for entry, data, styling and verification owners.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ...core import weight_plot
from ...core.themes import DEFAULT_TOKENS


class WeightChart(QWidget):
    entry_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._tokens = DEFAULT_TOKENS
        self._points = []
        self._trend = []
        self._positions = []
        self._contrast = False

    def set_tokens(self, tokens, contrast=False):
        self._tokens, self._contrast = tokens, contrast
        self.update()

    def set_data(self, points, trend):
        self._points, self._trend = points, trend
        self._positions = []
        self.setToolTip("")
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = self._tokens
        painter.fillRect(self.rect(), QColor(t["BG_SECONDARY"]))
        painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
        if not self._points:
            painter.drawText(self.rect(), Qt.AlignCenter, "No measurements in this range")
            return
        rect = QRectF(48, 16, max(1, self.width() - 64), max(1, self.height() - 66))
        xs = [point.x for point in self._points]
        ys = [point.y for point in self._points + self._trend]
        first, last = min(xs), max(xs)
        if first == last:
            first, last = first - 0.5, last + 0.5
        low, high = min(ys), max(ys)
        pad = max(0.3, (high - low) * 0.12)
        low, high = low - pad, high + pad

        def project(point):
            return QPointF(rect.left() + (point.x - first) / (last - first) * rect.width(),
                           rect.bottom() - (point.y - low) / (high - low) * rect.height())

        grid = QColor(t["TEXT_SECONDARY"])
        grid.setAlpha(130 if self._contrast else 45)
        for fraction in (0, 0.5, 1):
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
            painter.drawText(QRectF(0, y - 10, 42, 20), Qt.AlignRight | Qt.AlignVCenter,
                             f"{low + (high - low) * fraction:.1f}")
        painter.drawText(QRectF(rect.left(), rect.bottom() + 8, rect.width() / 2, 20),
                         Qt.AlignLeft, weight_plot.date_label(min(xs), "%d %b"))
        painter.drawText(QRectF(rect.center().x(), rect.bottom() + 8, rect.width() / 2, 20),
                         Qt.AlignRight, weight_plot.date_label(max(xs), "%d %b"))
        years = weight_plot.year_ticks(first, last)
        label_right = float("-inf")
        for ordinal, year in years:
            x = rect.left() + (ordinal - first) / (last - first) * rect.width()
            painter.setPen(QPen(grid, 0.7))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            width = painter.fontMetrics().horizontalAdvance(str(year)) + 4
            center = max(rect.left() + width / 2, min(rect.right() - width / 2, x))
            if center - width / 2 < label_right + 10:
                continue
            painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
            painter.drawText(QRectF(center - width / 2, rect.bottom() + 27, width, 20), Qt.AlignCenter, str(year))
            label_right = center + width / 2
        if not years:
            painter.drawText(QRectF(rect.left(), rect.bottom() + 27, rect.width(), 20),
                             Qt.AlignLeft, weight_plot.date_label(min(xs), "%Y"))
        painter.save()
        painter.setClipRect(rect.adjusted(-4, -4, 4, 4))
        self._positions = []
        painter.setPen(Qt.NoPen)
        color = QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"])
        color.setAlpha(230 if self._contrast else 170)
        painter.setBrush(color)
        for point in self._points:
            position = project(point)
            self._positions.append((position, point))
            painter.drawEllipse(position, 2.1, 2.1)
        # Keep the themed trend legible above dense, neutral measurements.
        if self._trend:
            path = QPainterPath()
            path.moveTo(project(self._trend[0]))
            for point in self._trend[1:]:
                path.lineTo(project(point))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(t["ACCENT"]), 3 if self._contrast else 2.2))
            painter.drawPath(path)
        painter.restore()

    def _near(self, position):
        if not self._positions:
            return None
        distance, point = min((((position.x() - xy.x()) ** 2 + (position.y() - xy.y()) ** 2, point)
                               for xy, point in self._positions), key=lambda item: item[0])
        return point if distance <= 144 else None

    def mouseMoveEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        self.setCursor(Qt.PointingHandCursor if point else Qt.ArrowCursor)
        self.setToolTip(f"{point.row['measured_at'][:10]}\n{str(point.y).removesuffix('.0')} kg" if point else "")
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        if event.button() == Qt.LeftButton and point and point.row.get("uid"):
            self.entry_clicked.emit(point.row["uid"])
        super().mousePressEvent(event)


