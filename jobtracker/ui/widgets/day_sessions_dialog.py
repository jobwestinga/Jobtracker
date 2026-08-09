"""
Sessions for a single logical day (opened from a graph day or an agenda column).

This is a full little editor: the selected session can be edited (including
moving it to another subject), duplicated to today, or deleted, without leaving
the day view. "Open subject history…" still jumps to the per-subject manager and
preselects the same session there.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

from .manage_sessions_dialog import ManageSessionsDialog
from .session_dialog import SessionDialog, apply_session_edits
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    information,
    open_dialog,
    question,
    warning,
)

NAME_WIDTH = 16


def _fmt(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _color_dot(color: str, size: int = 10) -> QIcon:
    """A small filled circle used to tie each row to its subject colour."""
    pixmap = QPixmap(size + 4, size + 4)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(2, 2, size, size)
    painter.end()
    return QIcon(pixmap)


class DaySessionsDialog(InlineDialog):
    def __init__(self, service, day: date, parent=None, select_session_id=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.service = service
        self.day = day
        self.setWindowTitle(f"Sessions · {day.isoformat()}")
        self.setMinimumSize(560, 440)
        self._build_ui()
        self._reload(select_session_id=select_session_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        self._total_lbl = QLabel("")
        self._total_lbl.setStyleSheet("font-weight: 700;")
        layout.addWidget(self._total_lbl)

        self._list = QListWidget()
        # Monospace keeps the time / duration / subject columns aligned.
        self._list.setFont(QFont("SF Mono", 11))
        self._list.itemDoubleClicked.connect(lambda _item: self._edit_selected())
        layout.addWidget(self._list, 1)

        self._per_subject_lbl = QLabel("")
        self._per_subject_lbl.setWordWrap(True)
        self._per_subject_lbl.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._per_subject_lbl)

        row = QHBoxLayout()
        row.setSpacing(8)

        edit_btn = QPushButton("✎ Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setToolTip("Edit this session (double-click a row, or press Enter)")
        edit_btn.clicked.connect(self._edit_selected)
        row.addWidget(edit_btn)

        dup_btn = QPushButton("⧉ Duplicate")
        dup_btn.setCursor(Qt.PointingHandCursor)
        dup_btn.setToolTip("Copy this session to today at the same clock time")
        dup_btn.clicked.connect(self._duplicate_selected)
        row.addWidget(dup_btn)

        del_btn = QPushButton("✕ Delete")
        del_btn.setObjectName("dangerBtn")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._delete_selected)
        row.addWidget(del_btn)

        layout.addLayout(row)

        bottom = QHBoxLayout()
        history_btn = QPushButton("Open subject history…")
        history_btn.setCursor(Qt.PointingHandCursor)
        history_btn.setToolTip(
            "Show every session of this subject, with this one selected"
        )
        history_btn.clicked.connect(self._open_subject_history)
        bottom.addWidget(history_btn)
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        # Enter edits the selected row rather than closing the dialog.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    # ── keys ─────────────────────────────────────────────────────────────
    def _enter_edits_selection(self, event) -> bool:
        return (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and self._list.currentItem() is not None
        )

    def claims_inline_key(self, event) -> bool:
        return self._enter_edits_selection(event)

    def handle_inline_key(self, event) -> bool:
        if self._enter_edits_selection(event):
            self._edit_selected()
            return True
        return False

    # ── data ─────────────────────────────────────────────────────────────
    def _reload(self, select_session_id=None) -> None:
        previous = select_session_id
        if previous is None:
            current = self._list.currentItem()
            if current is not None:
                previous = (current.data(Qt.UserRole) or {}).get("session_id")

        self._list.clear()
        sessions = sorted(
            self.service.get_sessions_for_logical_day(self.day),
            key=lambda item: item.get("start_time", ""),
        )
        total = sum(s.get("duration_seconds", 0) for s in sessions)
        self._total_lbl.setText(
            f"{self.day.isoformat()} — {_fmt(total)} across {len(sessions)} session(s)"
        )

        target_row = 0
        for index, session in enumerate(sessions):
            try:
                start = datetime.fromisoformat(session["start_time"]).strftime("%H:%M")
                end = (
                    datetime.fromisoformat(session["end_time"]).strftime("%H:%M")
                    if session.get("end_time")
                    else "  …  "
                )
            except (ValueError, KeyError):
                start, end = "??:??", "??:??"
            name = session.get("subject_name", "")
            if len(name) > NAME_WIDTH:
                name = name[: NAME_WIDTH - 1] + "…"
            note = f"  — {session['note']}" if session.get("note") else ""
            live = "  (running)" if session.get("session_id") is None else ""
            item = QListWidgetItem(
                f"{start}–{end}  {_fmt(session.get('duration_seconds', 0)):>7}  "
                f"{name:<{NAME_WIDTH}}{note}{live}"
            )
            item.setIcon(_color_dot(session.get("color", "#3B82F6")))
            item.setData(Qt.UserRole, session)
            self._list.addItem(item)
            if previous is not None and session.get("session_id") == previous:
                target_row = index

        self._per_subject_lbl.setText(self._per_subject_html(sessions))

        if self._list.count():
            self._list.setCurrentRow(target_row)
            self._list.scrollToItem(self._list.item(target_row))

    @staticmethod
    def _per_subject_html(sessions: list[dict]) -> str:
        totals: dict[tuple[str, str], int] = {}
        for session in sessions:
            key = (session.get("subject_name", ""), session.get("color", "#3B82F6"))
            totals[key] = totals.get(key, 0) + int(session.get("duration_seconds", 0))
        if not totals:
            return "No sessions on this day."
        parts = [
            f"<span style='color:{color};'>■</span> {name} {_fmt(seconds)}"
            for (name, color), seconds in sorted(
                totals.items(), key=lambda kv: kv[1], reverse=True
            )
        ]
        return "&nbsp;&nbsp;&nbsp;".join(parts)

    # ── selection helpers ────────────────────────────────────────────────
    def _selected(self, action: str) -> dict | None:
        item = self._list.currentItem()
        if item is None:
            information(self, "No selection", f"Pick a session to {action}.")
            return None
        session = item.data(Qt.UserRole) or {}
        if session.get("session_id") is None:
            warning(
                self,
                "Session Still Running",
                "That session is still being tracked. Stop it first, then it can "
                f"be {action}d here.",
            )
            return None
        return session

    # ── actions ──────────────────────────────────────────────────────────
    def _edit_selected(self) -> None:
        selected = self._selected("edit")
        if selected is None:
            return
        session = self.service.get_session(selected["session_id"])
        if session is None:
            self._reload()
            return
        dialog = SessionDialog(
            self,
            session,
            service=self.service,
            current_subject_id=session.subject_id,
        )
        open_dialog(
            dialog,
            lambda result, dlg: self._finish_edit(session, result, dlg),
        )

    def _finish_edit(self, session, result: int, dialog) -> None:
        if result != QDialog.Accepted:
            return
        apply_session_edits(self.service, session, dialog.get_data())
        self._reload(select_session_id=session.id)

    def _duplicate_selected(self) -> None:
        selected = self._selected("duplicate")
        if selected is None:
            return
        copy = self.service.duplicate_session(selected["session_id"], to="today")
        if copy is None:
            warning(
                self,
                "Nothing Duplicated",
                "That copy would start in the future, so no session was created.",
            )
            return
        information(
            self,
            "Session Duplicated",
            f"Copied to today at the same clock time ({_fmt(copy.duration_seconds)}).",
        )
        self._reload(select_session_id=copy.id)

    def _delete_selected(self) -> None:
        selected = self._selected("delete")
        if selected is None:
            return
        session_id = selected["session_id"]
        question(
            self,
            "Confirm Delete",
            "Delete this session permanently?",
            lambda answer: self._finish_delete(session_id, answer),
        )

    def _finish_delete(self, session_id: int, answer) -> None:
        if answer != QMessageBox.Yes:
            return
        self.service.delete_session(session_id)
        self._reload()

    def _open_subject_history(self) -> None:
        item = self._list.currentItem()
        if item is None:
            information(self, "No selection", "Pick a session first.")
            return
        session = item.data(Qt.UserRole) or {}
        subject_id = session.get("subject_id")
        if subject_id is None:
            return
        dialog = ManageSessionsDialog(
            subject_id,
            self.service,
            self,
            select_session_id=session.get("session_id"),
        )
        open_dialog(dialog, lambda _result, _dialog: self._reload())
