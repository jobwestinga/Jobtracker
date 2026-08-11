"""
Sessions for a single logical day (opened from a graph day or an agenda column).

Same machinery as the per-subject Sessions dialog — list, selection, and the
edit / duplicate / delete / nudge actions all come from :mod:`session_list`.
"""

from __future__ import annotations

from datetime import date
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import session_list
from .session_list import (
    SessionListView,
    build_move_row,
    format_duration,
    resolve_tokens,
)
from .dialog_utils import InlineDialog, configure_window_modal


class DaySessionsDialog(InlineDialog):
    def __init__(self, service, day: date, parent=None, select_session_id=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.service = service
        self.day = day
        self.setWindowTitle(f"Sessions · {day.isoformat()}")
        self.setMinimumSize(560, 470)
        self._build_ui()
        self._reload(select_session_id=select_session_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        self._total_lbl = QLabel("")
        self._total_lbl.setStyleSheet("font-weight: 700;")
        layout.addWidget(self._total_lbl)

        self._list = SessionListView(show_subject=True)
        self._list.apply_tokens(resolve_tokens(self))
        self._list.session_activated.connect(self._edit_selected)
        layout.addWidget(self._list, 1)

        self._per_subject_lbl = QLabel("")
        self._per_subject_lbl.setWordWrap(True)
        self._per_subject_lbl.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._per_subject_lbl)

        move_label = QLabel("Move selected session:")
        move_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(move_label)
        layout.addLayout(build_move_row(self._shift_selected))

        row = QHBoxLayout()
        row.setSpacing(8)

        edit_btn = QPushButton("✎ Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setToolTip("Edit this session (double-click a row, or press Enter)")
        edit_btn.clicked.connect(self._edit_selected)
        row.addWidget(edit_btn)

        dup_btn = QPushButton("⧉ Duplicate to Today")
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
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    # ── keys ─────────────────────────────────────────────────────────────
    def claims_inline_key(self, event) -> bool:
        return session_list.enter_edits_selection(self._list, event)

    def handle_inline_key(self, event) -> bool:
        if session_list.enter_edits_selection(self._list, event):
            self._edit_selected()
            return True
        return False

    # ── data ─────────────────────────────────────────────────────────────
    def _reload(self, select_session_id=None) -> None:
        sessions = sorted(
            self.service.get_sessions_for_logical_day(self.day),
            key=lambda item: item.get("start_time", ""),
        )
        total = sum(s.get("duration_seconds", 0) for s in sessions)
        self._total_lbl.setText(
            f"{self.day.isoformat()} — {format_duration(total)} "
            f"across {len(sessions)} session(s)"
        )
        self._list.set_sessions(sessions, select_session_id=select_session_id)
        self._per_subject_lbl.setText(self._per_subject_html(sessions))

    @staticmethod
    def _per_subject_html(sessions: list[dict]) -> str:
        totals: dict[tuple[str, str], int] = {}
        for session in sessions:
            key = (session.get("subject_name", ""), session.get("color", "#3B82F6"))
            totals[key] = totals.get(key, 0) + int(session.get("duration_seconds", 0))
        if not totals:
            return "No sessions on this day."
        parts = [
            f"<span style='color:{color};'>■</span> {escape(name)} "
            f"{format_duration(seconds)}"
            for (name, color), seconds in sorted(
                totals.items(), key=lambda kv: kv[1], reverse=True
            )
        ]
        return "&nbsp;&nbsp;&nbsp;".join(parts)

    def _selected(self, action: str) -> dict | None:
        session = self._list.selected_session()
        if not session_list.require_editable(self, session, action):
            return None
        return session

    # ── actions ──────────────────────────────────────────────────────────
    def _edit_selected(self) -> None:
        selected = self._selected("edit")
        if selected is None:
            return
        session_id = selected["session_id"]
        session_list.edit_session(
            self,
            self.service,
            session_id,
            lambda: self._reload(select_session_id=session_id),
        )

    def _duplicate_selected(self) -> None:
        selected = self._selected("duplicate")
        if selected is None:
            return
        session_list.duplicate_session(
            self,
            self.service,
            selected["session_id"],
            lambda session_id: self._reload(select_session_id=session_id),
            to="today",
        )

    def _shift_selected(self, seconds: int) -> None:
        selected = self._selected("move")
        if selected is None:
            return
        session_list.shift_session(
            self,
            self.service,
            selected["session_id"],
            seconds,
            lambda session_id: self._reload(select_session_id=session_id),
        )

    def _delete_selected(self) -> None:
        selected = self._selected("delete")
        if selected is None:
            return
        session_list.delete_session(
            self, self.service, selected["session_id"], self._reload
        )
