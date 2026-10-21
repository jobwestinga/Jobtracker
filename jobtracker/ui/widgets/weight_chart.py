"""Native weight chart presentation; pure fitting/axes live in core.weight_plot.

WeightChart remains importable from weight_view for compatibility.
See docs/features/weight.md for entry, data, styling and verification owners.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ...core import weight_plot
from ...core.themes import DEFAULT_TOKENS


class WeightChart(QWidget):
    entry_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(195)
        self.setMouseTracking(True)
        self._tokens = DEFAULT_TOKENS
        self._points = []
        self._trend = []
        self._positions = []
        self._hovered_point = None
        self._contrast = False
        self._show_points = True

    def set_tokens(self, tokens, contrast=False):
        self._tokens, self._contrast = tokens, contrast
        self.update()

    def set_show_points(self, show: bool):
        self._show_points = bool(show)
        self.update()

    def set_data(self, points, trend):
        self._points, self._trend = points, trend
        self._positions = []
        self._hovered_point = None
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
        rect = QRectF(48, 16, max(1, self.width() - 64), max(1, self.height() - 62))
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
        grid.setAlpha(130 if self._contrast else 40)
        for fraction in (0, 0.5, 1):
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
            painter.drawText(QRectF(0, y - 10, 42, 20), Qt.AlignRight | Qt.AlignVCenter,
                             f"{low + (high - low) * fraction:.1f}")
        painter.drawText(QRectF(rect.left(), rect.bottom() + 6, rect.width() / 2, 18),
                         Qt.AlignLeft, weight_plot.date_label(min(xs), "%d %b"))
        painter.drawText(QRectF(rect.center().x(), rect.bottom() + 6, rect.width() / 2, 18),
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
            painter.drawText(QRectF(center - width / 2, rect.bottom() + 24, width, 18), Qt.AlignCenter, str(year))
            label_right = center + width / 2
        if not years:
            painter.drawText(QRectF(rect.left(), rect.bottom() + 24, rect.width(), 18),
                             Qt.AlignLeft, weight_plot.date_label(min(xs), "%Y"))
        painter.save()
        painter.setClipRect(rect.adjusted(-4, -4, 4, 4))

        accent_color = QColor(t.get("ACCENT", "#38BDF8"))
        # Draw translucent vertical area-gradient fill under the trend curve (D5-A)
        if len(self._trend) >= 2:
            trend_pts = [project(point) for point in self._trend]
            area = QPainterPath()
            area.moveTo(trend_pts[0].x(), rect.bottom())
            for pt in trend_pts:
                area.lineTo(pt)
            area.lineTo(trend_pts[-1].x(), rect.bottom())
            area.closeSubpath()
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            top_fill = QColor(accent_color)
            top_fill.setAlpha(68 if not self._contrast else 90)
            bot_fill = QColor(accent_color)
            bot_fill.setAlpha(0)
            grad.setColorAt(0.0, top_fill)
            grad.setColorAt(1.0, bot_fill)
            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
            painter.drawPath(area)

        # Adapt dot size & layering to point density so dense ranges ("All history" / "1Y")
        # stay clean and never bury the trend curve.
        self._positions = [(project(point), point) for point in self._points]
        px_per_point = rect.width() / max(1, len(self._points))
        dense_points = px_per_point < 5.5

        def _draw_dots():
            if not self._show_points:
                return
            dot_fill = QColor(t["TEXT_PRIMARY"] if self._contrast else "#E2E8F0")
            if dense_points:
                dot_fill.setAlpha(180 if self._contrast else 115)
                painter.setPen(Qt.NoPen)
                painter.setBrush(dot_fill)
                for position, point in self._positions:
                    if self._hovered_point is not point:
                        painter.drawEllipse(position, 1.35, 1.35)
            else:
                radius = 2.6 if px_per_point >= 11.0 else 1.9
                dot_fill.setAlpha(245 if self._contrast else 225)
                ring_pen = QPen(QColor(t["BG_SECONDARY"]), 1.1 if px_per_point >= 11.0 else 0.7)
                painter.setPen(ring_pen)
                painter.setBrush(dot_fill)
                for position, point in self._positions:
                    if self._hovered_point is not point:
                        painter.drawEllipse(position, radius, radius)

        if dense_points:
            _draw_dots()

        # Trend line
        if self._trend:
            path = QPainterPath()
            path.moveTo(project(self._trend[0]))
            for point in self._trend[1:]:
                path.lineTo(project(point))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(accent_color, 3.0 if self._contrast else 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        if not dense_points:
            _draw_dots()

        # Interactive hover crosshair + highlighted dot
        if self._hovered_point is not None:
            hp = project(self._hovered_point)
            cross_pen = QPen(accent_color, 1.0, Qt.DashLine)
            painter.setPen(cross_pen)
            painter.drawLine(QPointF(hp.x(), rect.top()), QPointF(hp.x(), rect.bottom()))
            painter.setPen(QPen(QColor(t["BG_SECONDARY"]), 1.4))
            painter.setBrush(accent_color)
            painter.drawEllipse(hp, 4.4, 4.4)

        painter.restore()

        if self._hovered_point is not None:
            hp = project(self._hovered_point)
            date_txt = self._hovered_point.row.get("measured_at", "")[:10]
            val_txt = f"{str(self._hovered_point.y).removesuffix('.0')} kg"
            label_txt = f"{date_txt} · {val_txt}"
            painter.setFont(QFont("SF Pro Text", 10, QFont.DemiBold))
            fm = painter.fontMetrics()
            pill_w = fm.horizontalAdvance(label_txt) + 16
            pill_h = 22
            pill_x = max(rect.left(), min(rect.right() - pill_w, hp.x() - pill_w / 2))
            pill_y = max(4.0, hp.y() - pill_h - 8)
            pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)
            painter.setPen(QPen(accent_color, 1.2))
            painter.setBrush(QColor(t["BG_PRIMARY"]))
            painter.drawRoundedRect(pill_rect, 6, 6)
            painter.setPen(QColor(t["TEXT_PRIMARY"]))
            painter.drawText(pill_rect, Qt.AlignCenter, label_txt)

    def _near(self, position):
        if not self._positions:
            return None
        distance, point = min((((position.x() - xy.x()) ** 2 + (position.y() - xy.y()) ** 2, point)
                               for xy, point in self._positions), key=lambda item: item[0])
        return point if distance <= 169 else None

    def mouseMoveEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        if point is not self._hovered_point:
            self._hovered_point = point
            self.update()
        self.setCursor(Qt.PointingHandCursor if point else Qt.ArrowCursor)
        self.setToolTip(f"{point.row['measured_at'][:10]}\n{str(point.y).removesuffix('.0')} kg" if point else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if self._hovered_point is not None:
            self._hovered_point = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        if event.button() == Qt.LeftButton and point and point.row.get("uid"):
            self.entry_clicked.emit(point.row["uid"])
        super().mousePressEvent(event)


