"""
Agenda-style timeline view for daily tracked sessions.

Sessions are positioned using logical-day "agenda hours" computed by the service
(see ``timeutils.agenda_hour``): work that happens after midnight but belongs to
the previous logical day is placed at hours 24..27 so it renders at the BOTTOM of
that day's column instead of padding empty space at the top. Axis labels above 24
read like ``00:00 (+1)``.

Each session dict carries: ``day`` (logical-day iso), ``start_h`` / ``end_h``
(floats, may exceed 24), ``color``, ``subject_name``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout

from ...core.timeutils import agenda_hour_label


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


class _AgendaCanvas(QWidget):
    """Inner canvas painted inside a horizontal scroll area."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self._sessions: list[dict] = []
        self._day_keys: list[str] = []
        self._tokens: dict = {}
        self._hour_start: int = 6
        self._hour_end: int = 23
        self._fit_width: bool = True
        self.setMinimumHeight(360)

    # ── public API ────────────────────────────────────────────────────────
    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_data(
        self,
        sessions: list[dict],
        day_keys: list[str],
        hour_start: int = 6,
        hour_end: int = 23,
        fit_width: bool = True,
    ) -> None:
        self._sessions = sessions
        self._day_keys = day_keys
        # Agenda axis may extend past midnight (up to 27 == 03:00 (+1)).
        self._hour_start = max(0, min(26, hour_start))
        self._hour_end = max(self._hour_start + 1, min(27, hour_end))
        self._fit_width = fit_width

        left_margin = 56
        right_margin = 14
        gap = 6
        min_col_width = 40
        day_count = max(1, len(day_keys))
        required_width = left_margin + right_margin + day_count * min_col_width + (day_count - 1) * gap

        if not self._fit_width and self._day_keys:
            min_width = max(400, 60 + len(day_keys) * 80)
            self.setMinimumWidth(max(min_width, required_width))
        else:
            self.setMinimumWidth(100)

        self.update()

    # ── painting ──────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
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

        if not self._day_keys:
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.drawText(self.rect(), Qt.AlignCenter, "No tracked data yet")
            return

        left_margin = 56
        top_margin = 30
        bottom_margin = 30
        right_margin = 14
        chart = QRectF(
            left_margin,
            top_margin,
            max(80, self.width() - left_margin - right_margin),
            max(120, self.height() - top_margin - bottom_margin),
        )

        hour_count = self._hour_end - self._hour_start
        if hour_count <= 0:
            return

        # Hour grid lines + labels (labels above 24 read "00:00 (+1)").
        p.setFont(QFont("SF Pro Text", 8))
        for h in range(self._hour_start, self._hour_end + 1):
            frac = (h - self._hour_start) / hour_count
            y = chart.top() + frac * chart.height()
            p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 70), 0.5))
            p.drawLine(int(chart.left()), int(y), int(chart.right()), int(y))
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.drawText(
                QRectF(0, y - 7, left_margin - 4, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                agenda_hour_label(h),
            )

        # Day columns.
        day_count = max(1, len(self._day_keys))
        gap = 6
        if self._fit_width and day_count > 15:
            gap = 2
        total_gap = gap * (day_count - 1)
        col_width = (chart.width() - total_gap) / day_count
        if not self._fit_width:
            col_width = max(40, col_width)
        else:
            col_width = max(2, col_width)

        # Group sessions by logical day.
        day_sessions: dict[str, list[dict]] = {d: [] for d in self._day_keys}
        for sess in self._sessions:
            day = sess.get("day", "")
            if day in day_sessions:
                day_sessions[day].append(sess)

        for idx, day_key in enumerate(self._day_keys):
            col_x = chart.left() + idx * (col_width + gap)

            try:
                from datetime import datetime
                day_label = datetime.fromisoformat(day_key).strftime("%m-%d")
            except ValueError:
                day_label = day_key
            p.setPen(QColor(t["TEXT_DIMMED"]))
            p.setFont(QFont("SF Pro Text", 8))
            p.drawText(
                QRectF(col_x - 6, chart.bottom() + 4, col_width + 12, 18),
                Qt.AlignCenter,
                day_label,
            )

            col_rect = QRectF(col_x, chart.top(), col_width, chart.height())
            p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 60), 0.5))
            p.setBrush(_with_alpha(t["BG_SECONDARY"], 60))
            p.drawRoundedRect(col_rect, 5, 5)

            # Build placement rects from precomputed agenda hours.
            sess_rects: list[dict] = []
            for sess in day_sessions[day_key]:
                start_h = max(self._hour_start, min(self._hour_end, float(sess.get("start_h", 0))))
                end_h = max(self._hour_start, min(self._hour_end, float(sess.get("end_h", 0))))
                if end_h <= start_h:
                    continue
                sess_rects.append(
                    {
                        "top_frac": (start_h - self._hour_start) / hour_count,
                        "bot_frac": (end_h - self._hour_start) / hour_count,
                        "color": sess.get("color", "#3B82F6"),
                        "name": sess.get("subject_name", ""),
                    }
                )

            # Push overlaps downward so they stay visible.
            sess_rects.sort(key=lambda x: x["top_frac"])
            current_bottom = 0.0
            for sr in sess_rects:
                if sr["top_frac"] < current_bottom:
                    dur = sr["bot_frac"] - sr["top_frac"]
                    sr["top_frac"] = current_bottom
                    sr["bot_frac"] = current_bottom + dur
                current_bottom = sr["bot_frac"]

            for sr in sess_rects:
                sy = chart.top() + sr["top_frac"] * chart.height()
                sh = max(2, (sr["bot_frac"] - sr["top_frac"]) * chart.height())

                rect = QRectF(col_x + 1, sy, col_width - 2, sh)
                clip = QPainterPath()
                clip.addRoundedRect(rect, 3, 3)

                p.save()
                p.setClipPath(clip)
                color = QColor(sr["color"])
                color.setAlpha(200)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRect(rect)

                if sh > 16 and col_width > 24:
                    p.setPen(QColor("#FFFFFF"))
                    p.setFont(QFont("SF Pro Text", 7, QFont.Medium))
                    text_rect = rect.adjusted(2, 1, -2, -1)
                    p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, sr["name"])

                p.restore()

                p.setPen(QPen(_with_alpha(sr["color"], 220), 0.8))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, 3, 3)


class AgendaViewWidget(QWidget):
    """Scrollable agenda timeline with a configurable visible hour range."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._canvas = _AgendaCanvas()
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
        sessions: list[dict],
        day_keys: list[str],
        hour_start: int = 6,
        hour_end: int = 23,
        fit_width: bool = True,
    ) -> None:
        self._scroll.setWidgetResizable(fit_width)
        self._canvas.set_data(sessions, day_keys, hour_start, hour_end, fit_width)
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))
