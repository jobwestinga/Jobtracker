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

from datetime import datetime
from html import escape

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout

from ...core.timeutils import agenda_hour_label
from ...core.themes import DEFAULT_TOKENS
from .paint_utils import HoverCard, with_alpha as _with_alpha

ADJACENT_MARGIN_HOURS = 30 / 3600


def _agenda_time_label(hours: float) -> str:
    """Clock label for a float agenda hour, e.g. 24.5 -> '00:30 (+1)'."""
    total_minutes = int(round(hours * 60))
    day_offset, minutes_in_day = divmod(total_minutes, 24 * 60)
    hh, mm = divmod(minutes_in_day, 60)
    suffix = " (+1)" if day_offset else ""
    return f"{hh:02d}:{mm:02d}{suffix}"


def _layout_session_blocks(
    sessions: list[dict],
    hour_start: float,
    hour_end: float,
) -> list[dict]:
    """Place sessions and group transitions separated by at most 30 seconds."""
    blocks: list[dict] = []
    for session in sessions:
        start_h = max(hour_start, min(hour_end, float(session.get("start_h", 0))))
        end_h = max(hour_start, min(hour_end, float(session.get("end_h", 0))))
        if end_h <= start_h:
            continue
        blocks.append(
            {
                "start_h": start_h,
                "end_h": end_h,
                "color": session.get("color", "#3B82F6"),
                "name": session.get("subject_name", ""),
                # Untouched values for the hover card (layout may nudge the
                # painted start_h/end_h to close sub-30s seams).
                "orig_start_h": float(session.get("start_h", 0)),
                "orig_end_h": float(session.get("end_h", 0)),
                "duration_seconds": max(0, int(session.get("duration_seconds", 0))),
            }
        )

    blocks.sort(key=lambda block: (block["start_h"], block["end_h"]))
    current_bottom = hour_start
    group_id = -1
    previous: dict | None = None
    placed: list[dict] = []
    for block in blocks:
        duration = block["end_h"] - block["start_h"]
        join_previous = bool(
            previous
            and abs(block["start_h"] - previous["end_h"]) <= ADJACENT_MARGIN_HOURS
        )
        if join_previous and previous is not None:
            # Fill or trim a sub-30-second seam so both colors share one shape.
            block["start_h"] = previous["end_h"]
            block["end_h"] = max(block["end_h"], block["start_h"] + 1 / 3600)
            block["group"] = previous["group"]
        else:
            group_id += 1
            if block["start_h"] < current_bottom:
                block["start_h"] = current_bottom
                block["end_h"] = min(hour_end, current_bottom + duration)
            block["group"] = group_id

        if block["end_h"] <= block["start_h"]:
            continue
        current_bottom = max(current_bottom, block["end_h"])
        previous = block
        placed.append(block)
    return placed


class _AgendaCanvas(QWidget):
    """Inner canvas painted inside a horizontal scroll area."""

    day_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._sessions: list[dict] = []
        self._day_keys: list[str] = []
        self._tokens: dict = {}
        self._hour_start: int = 6
        self._hour_end: int = 23
        self._fit_width: bool = True
        # (today_logical_iso, current_agenda_hour) or None.
        self._now_marker: tuple[str, float] | None = None
        # Rebuilt on every paint; read by the mouse handlers.
        self._block_hits: list[tuple[QRectF, dict, str]] = []
        self._column_hits: list[tuple[QRectF, str]] = []
        self._hover_card = HoverCard(self)
        self.setMinimumHeight(360)

    # ── public API ────────────────────────────────────────────────────────
    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self._hover_card.apply_tokens(tokens)
        self.update()

    def set_data(
        self,
        sessions: list[dict],
        day_keys: list[str],
        hour_start: int = 6,
        hour_end: int = 23,
        fit_width: bool = True,
        now_marker: tuple[str, float] | None = None,
    ) -> None:
        self._hover_card.hide()
        self._sessions = sessions
        self._day_keys = day_keys
        # Agenda axis may extend past midnight (up to 27 == 03:00 (+1)).
        self._hour_start = max(0, min(26, hour_start))
        self._hour_end = max(self._hour_start + 1, min(27, hour_end))
        self._fit_width = fit_width
        self._now_marker = now_marker

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

        t = self._tokens or DEFAULT_TOKENS
        self._block_hits = []
        self._column_hits = []

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

        # Current-time marker: a subtle horizontal line at "now" spanning the
        # timeline. Painted here (before the columns) so it sits BEHIND the
        # coloured session bars, like a faint gridline showing where we are.
        if self._now_marker is not None:
            now_day, now_hour = self._now_marker
            if (
                now_day in self._day_keys
                and self._hour_start <= now_hour <= self._hour_end
            ):
                y = chart.top() + (now_hour - self._hour_start) / hour_count * chart.height()
                p.setPen(QPen(_with_alpha(t["TEXT_DIMMED"], 115), 1.5))
                p.drawLine(
                    int(chart.left()), int(y), int(chart.right()), int(y)
                )

        # Group sessions by logical day.
        day_sessions: dict[str, list[dict]] = {d: [] for d in self._day_keys}
        for sess in self._sessions:
            day = sess.get("day", "")
            if day in day_sessions:
                day_sessions[day].append(sess)

        for idx, day_key in enumerate(self._day_keys):
            col_x = chart.left() + idx * (col_width + gap)

            try:
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
            self._column_hits.append((QRectF(col_rect), day_key))
            p.setPen(QPen(_with_alpha(t["BORDER_COLOR"], 60), 0.5))
            p.setBrush(_with_alpha(t["BG_SECONDARY"], 60))
            p.drawRoundedRect(col_rect, 5, 5)

            sess_rects = _layout_session_blocks(
                day_sessions[day_key], self._hour_start, self._hour_end
            )
            for sr in sess_rects:
                sr["top_frac"] = (
                    sr["start_h"] - self._hour_start
                ) / hour_count
                sr["bot_frac"] = (
                    sr["end_h"] - self._hour_start
                ) / hour_count

            groups: dict[int, list[dict]] = {}
            for session_rect in sess_rects:
                groups.setdefault(session_rect["group"], []).append(session_rect)

            for group in groups.values():
                group_top = min(sr["top_frac"] for sr in group)
                group_bottom = max(sr["bot_frac"] for sr in group)
                group_y = chart.top() + group_top * chart.height()
                group_h = max(2, (group_bottom - group_top) * chart.height())
                group_rect = QRectF(col_x + 1, group_y, col_width - 2, group_h)
                clip = QPainterPath()
                clip.addRoundedRect(group_rect, 3, 3)
                p.save()
                p.setClipPath(clip)
                for sr in group:
                    sy = chart.top() + sr["top_frac"] * chart.height()
                    sh = max(
                        2,
                        (sr["bot_frac"] - sr["top_frac"]) * chart.height(),
                    )
                    rect = QRectF(col_x + 1, sy, col_width - 2, sh)
                    self._block_hits.append((QRectF(rect), sr, day_key))
                    color = QColor(sr["color"])
                    color.setAlpha(210)
                    p.setPen(Qt.NoPen)
                    p.setBrush(color)
                    p.drawRect(rect)

                    if sh > 16 and col_width > 24:
                        p.setPen(QColor("#FFFFFF"))
                        p.setFont(QFont("SF Pro Text", 7, QFont.Medium))
                        text_rect = rect.adjusted(2, 1, -2, -1)
                        p.drawText(
                            text_rect,
                            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                            sr["name"],
                        )
                p.restore()

    # ── hover / click ─────────────────────────────────────────────────────
    def _block_at(self, x: float, y: float) -> tuple[dict, str] | None:
        for rect, block, day_key in reversed(self._block_hits):
            if rect.contains(x, y):
                return block, day_key
        return None

    def _day_at(self, x: float, y: float) -> str | None:
        for rect, day_key in self._column_hits:
            if rect.contains(x, y):
                return day_key
        return None

    @staticmethod
    def _block_html(block: dict, day_key: str) -> str:
        try:
            day_label = datetime.fromisoformat(day_key).strftime("%A, %d %B %Y")
        except ValueError:
            day_label = day_key
        start = _agenda_time_label(block.get("orig_start_h", block["start_h"]))
        end = _agenda_time_label(block.get("orig_end_h", block["end_h"]))
        hours = block.get("duration_seconds", 0) / 3600.0
        return (
            f"<b>{escape(block.get('name', ''))}</b><br>"
            f"{day_label}<br>"
            f"{start}–{end} · {hours:.1f}h"
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        hit = self._block_at(pos.x(), pos.y())
        if hit is not None:
            block, day_key = hit
            self._hover_card.show_at(pos.x(), pos.y(), self._block_html(block, day_key))
            self.setCursor(Qt.PointingHandCursor)
        else:
            self._hover_card.hide()
            if self._day_at(pos.x(), pos.y()) is not None:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_card.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        day_key = self._day_at(event.position().x(), event.position().y())
        if day_key is not None:
            self.day_clicked.emit(day_key)
        super().mousePressEvent(event)


class AgendaViewWidget(QWidget):
    """Scrollable agenda timeline with a configurable visible hour range."""

    day_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._canvas = _AgendaCanvas()
        self._canvas.day_clicked.connect(self.day_clicked)
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
        now_marker: tuple[str, float] | None = None,
    ) -> None:
        self._scroll.setWidgetResizable(fit_width)
        self._canvas.set_data(
            sessions, day_keys, hour_start, hour_end, fit_width, now_marker
        )
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._canvas.setFixedHeight(max(360, self._scroll.viewport().height()))
