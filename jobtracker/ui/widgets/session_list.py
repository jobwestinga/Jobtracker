"""
One session list, one set of session actions — shared by every place that shows
sessions (the per-subject manager and the per-day view).

Both dialogs render rows, selection, and the edit / duplicate / delete / nudge
behaviour through this module, so reaching "the sessions menu" from a subject
card or from a graph lands on genuinely the same code, not a lookalike.

Rows are plain dicts:
    session_id, subject_id, subject_name, color, start_time, end_time,
    duration_seconds
``session_id`` is None only for the live session, which can be shown but never
edited.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QWidget,
)

from ...core.themes import DEFAULT_TOKENS
from .dialog_utils import information, open_dialog, question, warning

# NOTE: session_dialog imports build_move_row from here, so SessionDialog is
# imported lazily inside edit_session() to keep the module graph acyclic.

NAME_WIDTH = 16

# The nudge buttons shared by the editor and the session lists.
MOVE_STEPS: tuple[tuple[str, int], ...] = (
    ("−1h", -3600),
    ("−15m", -900),
    ("+15m", 900),
    ("+1h", 3600),
)


def resolve_tokens(widget: QWidget) -> dict:
    """Theme tokens for a widget, wherever it is nested.

    Dialogs opened from other dialogs have no ``_tokens`` on their immediate
    parent, which is why session lists used to look different depending on how
    they were opened. Walk up to the window instead.
    """
    current: QWidget | None = widget
    while current is not None:
        tokens = getattr(current, "_tokens", None)
        if tokens:
            return tokens
        current = current.parentWidget()
    window = widget.window() if widget is not None else None
    return getattr(window, "_tokens", None) or DEFAULT_TOKENS


def color_dot(color: str, size: int = 10) -> QIcon:
    """A small filled circle tying a row to its subject colour."""
    pixmap = QPixmap(size + 4, size + 4)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(2, 2, size, size)
    painter.end()
    return QIcon(pixmap)


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def format_session_row(
    session: dict, *, show_date: bool = False, show_subject: bool = False
) -> str:
    try:
        start_dt = datetime.fromisoformat(session["start_time"])
        start = start_dt.strftime("%H:%M")
        end = (
            datetime.fromisoformat(session["end_time"]).strftime("%H:%M")
            if session.get("end_time")
            else "  …  "
        )
        day = start_dt.strftime("%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        start, end, day = "??:??", "??:??", "??????????"

    parts = []
    if show_date:
        parts.append(day)
    parts.append(f"{start}–{end}")
    parts.append(f"{format_duration(session.get('duration_seconds', 0)):>7}")
    if show_subject:
        name = session.get("subject_name", "")
        if len(name) > NAME_WIDTH:
            name = name[: NAME_WIDTH - 1] + "…"
        parts.append(f"{name:<{NAME_WIDTH}}")
    row = "  ".join(parts).rstrip()
    if session.get("session_id") is None:
        row += "  (running)"
    return row


def session_row(session, subject) -> dict:
    """Adapt a :class:`Session` model + its subject into a row dict."""
    return {
        "session_id": session.id,
        "subject_id": session.subject_id,
        "subject_name": getattr(subject, "name", ""),
        "color": getattr(subject, "color", "#3B82F6"),
        "start_time": session.start_time,
        "end_time": session.end_time,
        "duration_seconds": session.duration_seconds,
    }


class SessionListView(QListWidget):
    """The session list used everywhere: same rows, same selection, same keys."""

    session_activated = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        show_date: bool = False,
        show_subject: bool = False,
    ) -> None:
        super().__init__(parent)
        self._show_date = show_date
        self._show_subject = show_subject
        self.setFont(QFont("SF Mono", 11))
        self.itemDoubleClicked.connect(lambda _item: self.session_activated.emit())
        self.apply_tokens(resolve_tokens(self))

    def apply_tokens(self, tokens: dict) -> None:
        accent = tokens.get("ACCENT", "#3B82F6")
        # Build the translucent fill with rgba(): Qt reads an 8-digit hex as
        # #AARRGGBB, so "#3B82F644" would render GREEN, not a faded blue.
        tint = QColor(accent)
        fill = f"rgba({tint.red()}, {tint.green()}, {tint.blue()}, 68)"
        self.setStyleSheet(
            "QListWidget::item { padding: 5px 8px; border: 1px solid transparent;"
            " border-radius: 6px; }"
            f"QListWidget::item:selected {{ background: {fill};"
            f" border: 1.6px solid {accent}; color: palette(text); }}"
        )

    def set_sessions(self, rows: list[dict], select_session_id=None) -> None:
        """Rebuild the list, keeping (or forcing) which session is selected."""
        wanted = select_session_id
        if wanted is None:
            current = self.selected_session()
            if current is not None:
                wanted = current.get("session_id")

        self.clear()
        target_row = 0
        for index, session in enumerate(rows):
            item = QListWidgetItem(
                format_session_row(
                    session,
                    show_date=self._show_date,
                    show_subject=self._show_subject,
                )
            )
            item.setIcon(color_dot(session.get("color", "#3B82F6")))
            item.setData(Qt.UserRole, session)
            self.addItem(item)
            if wanted is not None and session.get("session_id") == wanted:
                target_row = index

        if self.count():
            self.setCurrentRow(target_row)
            self.scrollToItem(self.item(target_row))

    def selected_session(self) -> dict | None:
        item = self.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def selected_session_id(self):
        session = self.selected_session()
        return session.get("session_id") if session else None


def build_move_row(on_shift: Callable[[int], None]) -> QHBoxLayout:
    """The ±15m / ±1h nudge buttons, identical wherever they appear."""
    row = QHBoxLayout()
    row.setSpacing(6)
    for label, seconds in MOVE_STEPS:
        button = QPushButton(label)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(30)
        button.setProperty("no_drag", True)
        button.setToolTip(
            f"Move this session {'back' if seconds < 0 else 'forward'} "
            f"{abs(seconds) // 60} minutes"
        )
        button.clicked.connect(lambda _checked=False, s=seconds: on_shift(s))
        row.addWidget(button)
    return row


def enter_edits_selection(list_view: "SessionListView", event) -> bool:
    """True when Return/Enter should open the selected session for editing.

    Both session dialogs use this so Enter means the same thing in each.
    """
    return (
        event.key() in (Qt.Key_Return, Qt.Key_Enter)
        and list_view.selected_session() is not None
    )


# ── shared actions ───────────────────────────────────────────────────────────
def require_editable(host: QWidget, session: dict | None, action: str) -> bool:
    """Guard every action: something selected, and not the running session."""
    if session is None:
        information(host, "No Selection", f"Select a session to {action}.")
        return False
    if session.get("session_id") is None:
        warning(
            host,
            "Session Still Running",
            "That session is still being tracked. Stop it first, then you can "
            f"{action} it here.",
        )
        return False
    return True


def edit_session(host: QWidget, service, session_id: int, on_done: Callable) -> None:
    """Open the shared editor for one session (subject move included)."""
    from .session_dialog import SessionDialog, apply_session_edits

    session = service.get_session(session_id)
    if session is None:
        on_done()
        return
    dialog = SessionDialog(
        host,
        session,
        service=service,
        current_subject_id=session.subject_id,
    )

    def finished(result: int, dlg) -> None:
        if result == QDialog.Accepted:
            apply_session_edits(service, session, dlg.get_data())
        on_done()

    open_dialog(dialog, finished)


def duplicate_session(
    host: QWidget, service, session_id: int, on_done: Callable, to: str = "today"
) -> None:
    """Copy a session onto another logical day ("today" or "next_day")."""
    copy = service.duplicate_session(session_id, to=to)
    if copy is None:
        warning(host, "Nothing Duplicated", "That session could not be duplicated.")
        return
    on_done(copy.id)


def delete_session(
    host: QWidget, service, session_id: int, on_done: Callable
) -> None:
    def answered(answer) -> None:
        if answer == QMessageBox.Yes:
            service.delete_session(session_id)
            on_done()

    question(host, "Confirm Delete", "Delete this session permanently?", answered)


def shift_session(
    host: QWidget, service, session_id: int, seconds: int, on_done: Callable
) -> None:
    moved = service.shift_session(session_id, seconds)
    if moved is None:
        warning(host, "Could Not Move", "That session could not be moved.")
        return
    on_done(moved.id)
