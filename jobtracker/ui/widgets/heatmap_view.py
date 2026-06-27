"""
GitHub-style contributions heatmap of tracked time per logical day.

One cell per logical day (default boundary 03:00), laid out in week columns with
weekday rows (Monday at top). Cell intensity scales with tracked hours. Clicking a
cell emits the logical-day ISO date so the app can show that day's sessions.

Only ONE metric: tracked seconds per logical day. No streaks/insights.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

# Tracked-hours thresholds -> intensity level 0..4.
_LEVEL_THRESHOLDS_SECONDS = [0, 3600, 2 * 3600, 4 * 3600]  # >last => level 4
_LEVEL_ALPHA = [38, 80, 130, 185, 235]

CELL = 13
GAP = 3
TOP = 18
LEFT = 30


def _level(seconds: int) -> int:
    if seconds <= 0:
        return 0
    level = 1
    for threshold in _LEVEL_THRESHOLDS_SECONDS[1:]:
        if seconds >= threshold:
            level += 1
    return min(level, 4)


class _HeatmapCanvas(QWidget):
    day_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self._data: dict[str, int] = {}
        self._first_monday: date | None = None
        self._first_day: date | None = None
        self._last_day: date | None = None
        self._weeks = 0
        self._tokens: dict = {}
        self.setMinimumHeight(7 * (CELL + GAP) + TOP + 24)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_data(self, rows: list[dict]) -> None:
        self._data = {r["date"]: int(r.get("total_seconds", 0)) for r in rows}
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
        width = LEFT + max(1, self._weeks) * (CELL + GAP) + 10
        self.setMinimumWidth(width)
        self.update()

    def _cell_date(self, col: int, row: int) -> date | None:
        if self._first_monday is None:
            return None
        return self._first_monday + timedelta(days=col * 7 + row)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._tokens or {"ACCENT": "#3B82F6", "TEXT_DIMMED": "#475569",
                             "BORDER_COLOR": "#1E3050", "BG_SECONDARY": "#131D2E"}
        accent = t.get("ACCENT", "#3B82F6")
        empty = QColor(t.get("BORDER_COLOR", "#1E3050"))
        empty.setAlpha(45)

        if self._first_monday is None:
            p.setPen(QColor(t.get("TEXT_DIMMED", "#475569")))
            p.drawText(self.rect(), Qt.AlignCenter, "No tracked data yet")
            return

        # Weekday labels (Mon/Wed/Fri).
        p.setFont(QFont("SF Pro Text", 7))
        p.setPen(QColor(t.get("TEXT_DIMMED", "#475569")))
        for row, label in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            y = TOP + row * (CELL + GAP) + CELL
            p.drawText(QRectF(0, y - CELL, LEFT - 4, CELL), Qt.AlignRight | Qt.AlignVCenter, label)

        for col in range(self._weeks):
            # Month label at the top when a new month starts in this column.
            col_date = self._first_monday + timedelta(days=col * 7)
            if col == 0 or col_date.day <= 7:
                p.setPen(QColor(t.get("TEXT_DIMMED", "#475569")))
                p.drawText(
                    QRectF(LEFT + col * (CELL + GAP), 0, 3 * (CELL + GAP), TOP - 2),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    col_date.strftime("%b"),
                )
            for row in range(7):
                cell_date = self._cell_date(col, row)
                if (
                    cell_date is None
                    or self._first_day is None
                    or self._last_day is None
                    or cell_date < self._first_day
                    or cell_date > self._last_day
                ):
                    continue
                seconds = self._data.get(cell_date.isoformat(), 0)
                x = LEFT + col * (CELL + GAP)
                y = TOP + row * (CELL + GAP)
                rect = QRectF(x, y, CELL, CELL)
                if seconds > 0:
                    c = QColor(accent)
                    c.setAlpha(_LEVEL_ALPHA[_level(seconds)])
                else:
                    c = empty
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(rect, 2.5, 2.5)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._first_monday is None:
            return
        x = event.position().x() - LEFT
        y = event.position().y() - TOP
        if x < 0 or y < 0:
            return
        col = int(x // (CELL + GAP))
        row = int(y // (CELL + GAP))
        if row > 6 or col >= self._weeks:
            return
        cell_date = self._cell_date(col, row)
        if (
            cell_date is not None
            and self._first_day is not None
            and self._last_day is not None
            and self._first_day <= cell_date <= self._last_day
        ):
            self.day_clicked.emit(cell_date.isoformat())


class HeatmapWidget(QWidget):
    """Scrollable heatmap + intensity legend."""

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
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll.setMaximumHeight(180)

        self._legend_canvas = _LegendCanvas()
        self._hint = QLabel("Click a day to inspect or edit its sessions.")
        self._hint.setStyleSheet("font-size: 11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._scroll)
        layout.addWidget(self._legend_canvas)
        layout.addWidget(self._hint)
        layout.addStretch(1)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self._canvas.set_tokens(tokens)
        self._legend_canvas.set_tokens(tokens)
        self._hint.setStyleSheet(
            f"font-size: 11px; color: {tokens.get('TEXT_SECONDARY', '#94A3B8')};"
        )

    def set_data(self, rows: list[dict]) -> None:
        self._canvas.set_data(rows)
        # All-history views are most useful at the recent end.
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
        self.setFixedHeight(20)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._tokens or {"ACCENT": "#3B82F6", "TEXT_DIMMED": "#475569"}
        accent = t.get("ACCENT", "#3B82F6")
        p.setFont(QFont("SF Pro Text", 8))
        p.setPen(QColor(t.get("TEXT_DIMMED", "#475569")))
        p.drawText(QRectF(0, 0, 34, 18), Qt.AlignVCenter | Qt.AlignRight, "Less")
        x = 40
        for level in range(5):
            c = QColor(accent)
            c.setAlpha(_LEVEL_ALPHA[level])
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, 4, 11, 11), 2.5, 2.5)
            x += 14
        p.setPen(QColor(t.get("TEXT_DIMMED", "#475569")))
        p.drawText(QRectF(x + 2, 0, 100, 18), Qt.AlignVCenter | Qt.AlignLeft, "More (0–4h+)")
