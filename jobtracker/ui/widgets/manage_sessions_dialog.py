"""
Manage Sessions dialog — every closed session of one subject.

The list, the row format, the selection highlight and the edit / duplicate /
delete / nudge actions all come from :mod:`session_list`, so this dialog and the
per-day view are the same machinery with different data.
"""

from datetime import datetime, time, timedelta

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PySide6.QtCore import Qt
from ...services.tracker_service import TrackerService
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    open_dialog,
)
from .session_dialog import DurationDialog, SessionDialog
from . import session_list
from .session_list import SessionListView, build_move_row, resolve_tokens

# Quick-add durations, in minutes.
QUICK_DURATIONS = [
    ("15m", 15),
    ("30m", 30),
    ("1h", 60),
    ("1h 15m", 75),
    ("2h", 120),
]

QUICK_SLOTS = [(9, 11), (11, 13), (13, 15), (15, 17), (17, 19)]


class ManageSessionsDialog(InlineDialog):
    def __init__(
        self,
        subject_id: int,
        service: TrackerService,
        parent=None,
        select_session_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.subject_id = subject_id
        self.service = service

        subject = next(
            (s for s in service.get_all_subjects_including_archived() if s.id == subject_id),
            None,
        )
        self._subject = subject
        title = f"Sessions — {subject.name}" if subject else "Sessions"
        self.setWindowTitle(title)
        self.setMinimumSize(560, 600)
        self._build_ui()
        self._load(select_session_id=select_session_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        self._empty_lbl = QLabel(
            'No sessions recorded yet.\nClick "+ Add Session" to create one.'
        )
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(
            "font-style: italic; opacity: 0.5; padding: 30px 0;"
        )
        self._empty_lbl.hide()
        layout.addWidget(self._empty_lbl)

        self._list = SessionListView(show_date=True)
        self._list.apply_tokens(resolve_tokens(self))
        self._list.session_activated.connect(self._edit)
        layout.addWidget(self._list, 1)

        # ── Quick add: duration ending now ────────────────────────────────
        quick_label = QLabel("Quick Add (ending now):")
        quick_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(quick_label)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(4)
        for label, minutes in QUICK_DURATIONS:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setObjectName("primaryBtn")
            btn.setStyleSheet("font-size: 12px; padding: 6px 6px;")
            btn.clicked.connect(lambda _checked=False, m=minutes: self._quick_add(m))
            quick_row.addWidget(btn)

        custom_btn = QPushButton("Custom…")
        custom_btn.setCursor(Qt.PointingHandCursor)
        custom_btn.setMinimumHeight(32)
        custom_btn.setStyleSheet("font-size: 12px; padding: 6px 6px;")
        custom_btn.setToolTip("Enter any duration (hours and minutes)")
        custom_btn.clicked.connect(self._custom_duration)
        quick_row.addWidget(custom_btn)
        layout.addLayout(quick_row)

        # ── Quick add: fixed slots today ──────────────────────────────────
        slot_label = QLabel("Quick Add (time slot, today):")
        slot_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(slot_label)

        slot_row = QHBoxLayout()
        slot_row.setSpacing(6)
        for start_h, end_h in QUICK_SLOTS:
            btn = QPushButton(f"{start_h}–{end_h}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("font-size: 12px; padding: 6px 8px;")
            btn.clicked.connect(
                lambda _checked=False, sh=start_h, eh=end_h: self._quick_add_slot(sh, eh)
            )
            slot_row.addWidget(btn)
        layout.addLayout(slot_row)

        # ── Move the selected session ─────────────────────────────────────
        move_label = QLabel("Move selected session:")
        move_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(move_label)
        layout.addLayout(build_move_row(self._shift_selected))

        # ── Actions ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton("+ Add Session")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton("✎ Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setToolTip("Edit this session (double-click a row too)")
        edit_btn.clicked.connect(self._edit)
        btn_row.addWidget(edit_btn)

        dup_btn = QPushButton("⧉ Duplicate to Today")
        dup_btn.setCursor(Qt.PointingHandCursor)
        dup_btn.setToolTip("Copy this session to today at the same clock time")
        dup_btn.clicked.connect(lambda: self._duplicate("today"))
        btn_row.addWidget(dup_btn)

        dup_next_btn = QPushButton("⧉ +1 Day")
        dup_next_btn.setCursor(Qt.PointingHandCursor)
        dup_next_btn.setToolTip(
            "Copy this session to the day after it. The copy stays selected, so "
            "clicking again walks forward day by day."
        )
        dup_next_btn.clicked.connect(lambda: self._duplicate("next_day"))
        btn_row.addWidget(dup_next_btn)

        del_btn = QPushButton("✕ Delete")
        del_btn.setObjectName("dangerBtn")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._delete)
        btn_row.addWidget(del_btn)

        layout.addLayout(btn_row)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(36)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        # No default button: Enter edits the selected session here, exactly as
        # it does in the per-day list. Escape still closes the dialog.
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)

    # ── keys ─────────────────────────────────────────────────────────────
    def claims_inline_key(self, event) -> bool:
        return session_list.enter_edits_selection(self._list, event)

    def handle_inline_key(self, event) -> bool:
        if session_list.enter_edits_selection(self._list, event):
            self._edit()
            return True
        return False

    # ── Data ─────────────────────────────────────────────────────────────
    def _load(self, select_session_id: int | None = None) -> None:
        sessions = [
            s for s in self.service.get_sessions_for_subject(self.subject_id) if s.end_time
        ]
        if not sessions:
            self._list.set_sessions([])
            self._empty_lbl.show()
            self._list.hide()
            return

        self._empty_lbl.hide()
        self._list.show()
        rows = [session_list.session_row(s, self._subject) for s in sessions]
        self._list.set_sessions(rows, select_session_id=select_session_id)

    def _selected(self, action: str) -> dict | None:
        session = self._list.selected_session()
        if not session_list.require_editable(self, session, action):
            return None
        return session

    # ── Quick add ────────────────────────────────────────────────────────
    def _quick_add(self, minutes: int, end: datetime | None = None) -> None:
        """Create a session of exactly ``minutes``, ending now.

        The start is deliberately NOT clamped to midnight. Logical days run to
        03:00, so a session that begins before midnight and ends after it is
        normal and is attributed to the day it began (the agenda even paints it
        at hours 24..27). Clamping silently shortened sessions instead — a 2h
        quick-add at 00:30 used to store 30 minutes, and a 12h custom duration
        at 10:00 stored 10 hours.
        """
        end = end or datetime.now()
        start = end - timedelta(minutes=max(1, int(minutes)))
        created = self.service.add_session(self.subject_id, start, end)
        self._load(select_session_id=created.id if created else None)

    def _quick_add_slot(self, start_hour: int, end_hour: int) -> None:
        """Create a session for a fixed time slot on today's date."""
        today = datetime.now().date()
        start = datetime.combine(today, time(hour=start_hour))
        end = datetime.combine(today, time(hour=end_hour))
        created = self.service.add_session(self.subject_id, start, end)
        self._load(select_session_id=created.id if created else None)

    def _custom_duration(self) -> None:
        dialog = DurationDialog(self)
        open_dialog(dialog, self._finish_custom_duration)

    def _finish_custom_duration(self, result: int, dialog) -> None:
        if result != QDialog.Accepted:
            return
        self._quick_add(dialog.total_minutes())

    # ── Actions ──────────────────────────────────────────────────────────
    def _add(self) -> None:
        dlg = SessionDialog(self)
        open_dialog(dlg, self._finish_add)

    def _finish_add(self, result: int, dialog: QDialog) -> None:
        if result != QDialog.Accepted:
            return
        d = dialog.get_data()
        created = self.service.add_session(
            self.subject_id, d["start_time"], d["end_time"]
        )
        self._load(select_session_id=created.id if created else None)

    def _edit(self) -> None:
        selected = self._selected("edit")
        if selected is None:
            return
        session_id = selected["session_id"]
        session_list.edit_session(
            self,
            self.service,
            session_id,
            lambda: self._after_edit(session_id),
        )

    def _after_edit(self, session_id: int) -> None:
        session = self.service.get_session(session_id)
        # An edit can move the session to another subject: then it belongs to a
        # different list and simply disappears from this one.
        if session is not None and session.subject_id != self.subject_id:
            self._load()
            return
        self._load(select_session_id=session_id)

    def _duplicate(self, to: str) -> None:
        selected = self._selected("duplicate")
        if selected is None:
            return
        session_list.duplicate_session(
            self,
            self.service,
            selected["session_id"],
            lambda session_id: self._load(select_session_id=session_id),
            to=to,
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
            lambda session_id: self._load(select_session_id=session_id),
        )

    def _delete(self) -> None:
        selected = self._selected("delete")
        if selected is None:
            return
        session_list.delete_session(
            self, self.service, selected["session_id"], self._load
        )
