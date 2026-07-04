"""Large, theme-aware heatmap of tracked time per logical day."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.themes import DEFAULT_TOKENS
from .paint_utils import HoverCard

MIN_CELL = 14
MAX_CELL = 28
GAP = 4
TOP = 28
LEFT = 42
BOTTOM = 14

def _is_light(color: QColor) -> bool:
    return (
        0.2126 * color.red()
        + 0.7152 * color.green()
        + 0.0722 * color.blue()
    ) > 150


def _scale_colors(tokens: dict) -> tuple[QColor, QColor]:
    """Return visually quiet zero and unmistakable best-day colors."""
    background = QColor(tokens.get("BG_PRIMARY", "#0B1120"))
    low = QColor(tokens.get("BG_TERTIARY", "#1A2640"))
    high = QColor("#008F3B" if _is_light(background) else "#4DFF88")
    return low, high


def _intensity_ratio(seconds: int, scale_max_seconds: int) -> float:
    """Continuous 0..1 daily intensity; the busiest visible day is always 1."""
    if seconds <= 0 or scale_max_seconds <= 0:
        return 0.0
    return max(0.0, min(1.0, float(seconds) / float(scale_max_seconds)))


def _visual_ratio(ratio: float) -> float:
    """Lift positive intensities smoothly while preserving exact endpoints."""
    return max(0.0, min(1.0, float(ratio))) ** 0.82


def _mix_color(start: QColor, end: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        round(start.red() + (end.red() - start.red()) * ratio),
        round(start.green() + (end.green() - start.green()) * ratio),
        round(start.blue() + (end.blue() - start.blue()) * ratio),
    )


class _HeatmapCanvas(QWidget):
    day_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._data: dict[str, int] = {}
        self._first_monday: date | None = None
        self._first_day: date | None = None
        self._last_day: date | None = None
        self._weeks = 0
        self._scale_max_seconds = 0
        self._tokens: dict = {}
        self._hover_card = HoverCard(self)
        self.setMinimumHeight(TOP + BOTTOM + 7 * MIN_CELL + 6 * GAP)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self._hover_card.apply_tokens(tokens)
        self.update()

    def set_data(self, rows: list[dict]) -> None:
        self._hover_card.hide()
        self._data = {row["date"]: int(row.get("total_seconds", 0)) for row in rows}
        self._scale_max_seconds = max(self._data.values(), default=0)
        if rows:
            try:
                first = datetime.fromisoformat(rows[0]["date"]).date()
                last = datetime.fromisoformat(rows[-1]["date"]).date()
            except ValueError:
                first = last = date.today()
            self._first_monday = first - timedelta(days=first.weekday())
            self._first_day = first
            self._last_day = last
            self._weeks = ((last - self._first_monday).days // 7) + 1
        else:
            self._first_monday = None
            self._first_day = None
            self._last_day = None
            self._weeks = 0
        self.setMinimumWidth(
            LEFT + max(1, self._weeks) * (MAX_CELL + GAP) + 18
        )
        self.update()

    def _metrics(self) -> tuple[int, int, int]:
        available = max(7 * MIN_CELL, self.height() - TOP - BOTTOM - 6 * GAP)
        cell = max(MIN_CELL, min(MAX_CELL, available // 7))
        stride = cell + GAP
        grid_width = max(1, self._weeks) * stride - GAP
        origin_x = LEFT
        if grid_width + LEFT + 12 < self.width():
            origin_x = max(LEFT, int((self.width() - grid_width) / 2))
        grid_height = 7 * cell + 6 * GAP
        origin_y = max(TOP, int((self.height() - grid_height) / 2))
        return cell, origin_x, origin_y

    def _cell_date(self, column: int, row: int) -> date | None:
        if self._first_monday is None:
            return None
        return self._first_monday + timedelta(days=column * 7 + row)

    def _cell_at(self, x: float, y: float) -> date | None:
        if self._first_monday is None:
            return None
        cell, origin_x, origin_y = self._metrics()
        stride = cell + GAP
        local_x = x - origin_x
        local_y = y - origin_y
        if local_x < 0 or local_y < 0:
            return None
        column = int(local_x // stride)
        row = int(local_y // stride)
        if (
            column >= self._weeks
            or row > 6
            or local_x % stride > cell
            or local_y % stride > cell
        ):
            return None
        cell_date = self._cell_date(column, row)
        if (
            cell_date is None
            or self._first_day is None
            or self._last_day is None
            or not self._first_day <= cell_date <= self._last_day
        ):
            return None
        return cell_date

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        tokens = self._tokens or DEFAULT_TOKENS

        if self._first_monday is None:
            painter.setPen(QColor(tokens["TEXT_DIMMED"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "No tracked data yet")
            return

        cell, origin_x, origin_y = self._metrics()
        stride = cell + GAP
        low_color, high_color = _scale_colors(tokens)

        painter.setFont(QFont("SF Pro Text", 8))
        painter.setPen(QColor(tokens["TEXT_DIMMED"]))
        for row, label in ((0, "Mon"), (2, "Wed"), (4, "Fri"), (6, "Sun")):
            y = origin_y + row * stride
            painter.drawText(
                QRectF(0, y, origin_x - 7, cell),
                Qt.AlignRight | Qt.AlignVCenter,
                label,
            )

        for column in range(self._weeks):
            column_date = self._first_monday + timedelta(days=column * 7)
            if column == 0 or column_date.day <= 7:
                painter.setPen(QColor(tokens["TEXT_DIMMED"]))
                painter.drawText(
                    QRectF(origin_x + column * stride, origin_y - 24, 3 * stride, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    column_date.strftime("%b"),
                )

            for row in range(7):
                cell_date = self._cell_date(column, row)
                if (
                    cell_date is None
                    or self._first_day is None
                    or self._last_day is None
                    or cell_date < self._first_day
                    or cell_date > self._last_day
                ):
                    continue
                seconds = self._data.get(cell_date.isoformat(), 0)
                x = origin_x + column * stride
                y = origin_y + row * stride
                rect = QRectF(x, y, cell, cell)
                ratio = _intensity_ratio(seconds, self._scale_max_seconds)
                color = _mix_color(
                    low_color,
                    high_color,
                    _visual_ratio(ratio),
                )
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                radius = min(7.0, cell * 0.22)
                painter.drawRoundedRect(rect, radius, radius)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        cell_date = self._cell_at(event.position().x(), event.position().y())
        if cell_date is None:
            self._hover_card.hide()
        else:
            seconds = self._data.get(cell_date.isoformat(), 0)
            best = (
                "<br><span style='color:#22C55E;'>Busiest day in this view</span>"
                if seconds > 0 and seconds == self._scale_max_seconds
                else ""
            )
            self._hover_card.show_at(
                event.position().x(),
                event.position().y(),
                f"<b>{cell_date.strftime('%A, %d %B %Y')}</b>"
                f"<br>{seconds / 3600:.1f} tracked hours{best}",
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_card.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        cell_date = self._cell_at(event.position().x(), event.position().y())
        if cell_date is not None:
            self.day_clicked.emit(cell_date.isoformat())


class HeatmapWidget(QWidget):
    """Full-height scrollable heatmap with intensity legend."""

    day_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: dict = {}
        self._canvas = _HeatmapCanvas()
        self._canvas.day_clicked.connect(self.day_clicked)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        self._legend_canvas = _LegendCanvas()
        self._hint = QLabel(
            "Click a day to inspect its sessions. Scroll horizontally for older history."
        )
        self._hint.setStyleSheet("font-size: 11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._legend_canvas)
        layout.addWidget(self._hint)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self._canvas.set_tokens(tokens)
        self._legend_canvas.set_tokens(tokens)
        self._hint.setStyleSheet(
            f"font-size: 11px; color: {tokens.get('TEXT_SECONDARY', '#94A3B8')};"
        )

    def set_data(self, rows: list[dict]) -> None:
        self._canvas.set_data(rows)
        self._legend_canvas.set_scale_max(
            max((int(row.get("total_seconds", 0)) for row in rows), default=0)
        )
        QTimer.singleShot(
            0,
            lambda: self._scroll.horizontalScrollBar().setValue(
                self._scroll.horizontalScrollBar().maximum()
            ),
        )


class _LegendCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: dict = {}
        self._scale_max_seconds = 0
        self.setFixedHeight(24)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_scale_max(self, seconds: int) -> None:
        self._scale_max_seconds = max(0, int(seconds))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        tokens = self._tokens or DEFAULT_TOKENS
        painter.setFont(QFont("SF Pro Text", 8))
        painter.setPen(QColor(tokens["TEXT_DIMMED"]))
        painter.drawText(QRectF(0, 0, 28, 20), Qt.AlignVCenter | Qt.AlignRight, "0h")
        x = 35
        width = 132
        low_color, high_color = _scale_colors(tokens)
        gradient = QLinearGradient(x, 0, x + width, 0)
        gradient.setColorAt(0.0, low_color)
        gradient.setColorAt(1.0, high_color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(x, 4, width, 14), 4, 4)
        hours = self._scale_max_seconds / 3600
        best_label = f"Best day · {hours:.1f}h" if hours else "Best day"
        painter.setPen(QColor(tokens["TEXT_DIMMED"]))
        painter.drawText(
            QRectF(x + width + 8, 0, 140, 20),
            Qt.AlignVCenter | Qt.AlignLeft,
            best_label,
        )
