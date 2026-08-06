"""
Manage Sessions dialog — lists all closed sessions for a given subject
and allows add / edit / delete, plus quick-add buttons.
"""

from datetime import datetime, time, timedelta

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt
from ...services.tracker_service import TrackerService
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    information,
    open_dialog,
    question,
    warning,
)
from .session_dialog import SessionDialog


class ManageSessionsDialog(InlineDialog):
    def __init__(self, subject_id: int, service: TrackerService, parent=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.subject_id = subject_id
        self.service = service

        subject = next((s for s in service.get_all_subjects() if s.id == subject_id), None)
        title = f"Sessions — {subject.name}" if subject else "Sessions"
        self.setWindowTitle(title)
        self.setMinimumSize(560, 560)
        parent_tokens = getattr(parent, "_tokens", None)
        self._accent = (parent_tokens or {}).get("ACCENT", "#3B82F6")
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Empty-state label
        self._empty_lbl = QLabel('No sessions recorded yet.\nClick "+ Add Session" to create one.')
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet("font-style: italic; opacity: 0.5; padding: 30px 0;")
        self._empty_lbl.hide()
        layout.addWidget(self._empty_lbl)

        self._list = QListWidget()
        # Condensed single-line rows + clear selection outline.
        self._list.setStyleSheet(
            "QListWidget::item {"
            "  padding: 5px 8px; border: 1px solid transparent; border-radius: 6px;"
            "}"
            "QListWidget::item:selected {"
            f"  background: {self._accent}33; border: 1.4px solid {self._accent};"
            f"  color: palette(text);"
            "}"
        )
        layout.addWidget(self._list)

        # ── Quick-add: duration (ending now) ──────────────────────────────
        quick_label = QLabel("Quick Add (ending now):")
        quick_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(quick_label)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(4)

        durations = [
            ("15m", 15), ("30m", 30), ("45m", 45),
            ("1h", 60), ("1h 15m", 75), ("1h 30m", 90),
            ("2h", 120), ("2h 30m", 150),
        ]
        for label, minutes in durations:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setObjectName("primaryBtn")
            btn.setStyleSheet("font-size: 12px; padding: 6px 6px;")
            btn.clicked.connect(lambda checked, m=minutes: self._quick_add(m))
            quick_row.addWidget(btn)

        layout.addLayout(quick_row)

        # ── Quick-add: fixed time slots (today) ───────────────────────────
        slot_label = QLabel("Quick Add (time slot, today):")
        slot_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(slot_label)

        slot_row = QHBoxLayout()
        slot_row.setSpacing(6)

        slots = [(9, 11), (11, 13), (13, 15), (15, 17), (17, 19)]
        for start_h, end_h in slots:
            btn = QPushButton(f"{start_h}–{end_h}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("font-size: 12px; padding: 6px 8px;")
            btn.clicked.connect(
                lambda checked, sh=start_h, eh=end_h: self._quick_add_slot(sh, eh)
            )
            slot_row.addWidget(btn)

        layout.addLayout(slot_row)

        # ── Action buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton("+ Add Session")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton("✎ Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(self._edit)
        btn_row.addWidget(edit_btn)

        dup_btn = QPushButton("⧉ Duplicate")
        dup_btn.setCursor(Qt.PointingHandCursor)
        dup_btn.setToolTip("Copy this session to today at the same clock time")
        dup_btn.clicked.connect(lambda: self._duplicate("today"))
        btn_row.addWidget(dup_btn)

        dup_next_btn = QPushButton("⧉ +1 Day")
        dup_next_btn.setCursor(Qt.PointingHandCursor)
        dup_next_btn.setToolTip(
            "Copy this session to the day after it, same clock time. "
            "The copy is selected, so clicking again walks forward day by day."
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

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        close_btn.setAutoDefault(True)
        close_btn.setDefault(True)

    # ── Data ─────────────────────────────────────────────────────────────
    def _load(self, select_session_id: int | None = None) -> None:
        self._list.clear()
        sessions = self.service.get_sessions_for_subject(self.subject_id)
        closed = [s for s in sessions if s.end_time]

        if not closed:
            self._empty_lbl.show()
            self._list.hide()
            return

        self._empty_lbl.hide()
        self._list.show()

        for s in closed:
            h, rem = divmod(s.duration_seconds, 3600)
            m, _sec = divmod(rem, 60)
            dur = f"{h}h {m}m" if h else f"{m}m"
            start = s.start_time[:16].replace("T", "  ")
            line = f"{start}   ·   {dur}"
            if s.note:
                line += f"   —   {s.note}"

            item = QListWidgetItem(line)
            item.setData(Qt.UserRole, s)
            self._list.addItem(item)

        # Outline the first row by default so Edit/Delete have a clear target.
        # After a duplicate, select the copy instead: repeated "+1 Day" clicks
        # then walk forward one day at a time.
        if not self._list.count():
            return
        target_row = 0
        if select_session_id is not None:
            for row in range(self._list.count()):
                session = self._list.item(row).data(Qt.UserRole)
                if session is not None and session.id == select_session_id:
                    target_row = row
                    break
        self._list.setCurrentRow(target_row)
        self._list.scrollToItem(self._list.item(target_row))

    # ── Actions ──────────────────────────────────────────────────────────
    def _quick_add(self, minutes: int) -> None:
        """Create a session ending now that started *minutes* ago.

        Never rolls the start back across midnight — the session stays
        strictly on today.
        """
        end = datetime.now()
        start = end - timedelta(minutes=minutes)
        if start.date() < end.date():
            start = datetime.combine(end.date(), time.min)
        self.service.add_session(self.subject_id, start, end)
        self._load()

    def _quick_add_slot(self, start_hour: int, end_hour: int) -> None:
        """Create a session for a fixed time slot on today's date."""
        today = datetime.now().date()
        start = datetime.combine(today, time(hour=start_hour))
        end = datetime.combine(today, time(hour=end_hour))
        self.service.add_session(self.subject_id, start, end)
        self._load()

    def _add(self) -> None:
        dlg = SessionDialog(self)
        open_dialog(dlg, self._finish_add)

    def _finish_add(self, result: int, dialog: QDialog) -> None:
        if result != QDialog.Accepted:
            return
        d = dialog.get_data()
        self.service.add_session(
            self.subject_id, d["start_time"], d["end_time"], d["note"]
        )
        self._load()

    def _edit(self) -> None:
        sel = self._list.currentItem()
        if not sel:
            information(self, "No Selection", "Select a session to edit.")
            return
        session = sel.data(Qt.UserRole)
        if session.id is None:
            warning(self, "Invalid Session", "Selected session has no valid identifier.")
            return
        dlg = SessionDialog(
            self,
            session,
            service=self.service,
            current_subject_id=self.subject_id,
        )
        open_dialog(
            dlg,
            lambda result, dialog: self._finish_edit(
                session.id, result, dialog
            ),
        )

    def _finish_edit(
        self, session_id: int, result: int, dialog: QDialog
    ) -> None:
        if result != QDialog.Accepted:
            return
        d = dialog.get_data()
        target_subject_id = d.get("subject_id") or self.subject_id
        self.service.update_session(
            session_id,
            target_subject_id,
            d["start_time"],
            d["end_time"],
            d["note"],
        )
        self._load()
        if target_subject_id != self.subject_id:
            target = next(
                (
                    s
                    for s in self.service.get_all_subjects_including_archived()
                    if s.id == target_subject_id
                ),
                None,
            )
            information(
                self,
                "Session Moved",
                f'Session moved to "{target.name if target else "another subject"}".'
                "\nSame times, same duration — only the subject changed.",
            )

    def _duplicate(self, to: str) -> None:
        sel = self._list.currentItem()
        if not sel:
            information(self, "No Selection", "Select a session to duplicate.")
            return
        session = sel.data(Qt.UserRole)
        if session.id is None:
            warning(self, "Invalid Session", "Selected session has no valid identifier.")
            return

        copy = self.service.duplicate_session(session.id, to=to)
        if copy is None:
            warning(
                self,
                "Nothing Duplicated",
                "That copy would start in the future, so no session was created.\n"
                "Use Edit or + Add Session to enter times manually.",
            )
            return
        self._load(select_session_id=copy.id)

    def _delete(self) -> None:
        sel = self._list.currentItem()
        if not sel:
            information(self, "No Selection", "Select a session to delete.")
            return
        session = sel.data(Qt.UserRole)
        if session.id is None:
            warning(self, "Invalid Session", "Selected session has no valid identifier.")
            return
        question(
            self, "Confirm Delete",
            "Delete this session permanently?",
            lambda answer: self._finish_delete(session.id, answer),
        )

    def _finish_delete(
        self, session_id: int, answer: QMessageBox.StandardButton
    ) -> None:
        if answer != QMessageBox.Yes:
            return
        self.service.delete_session(session_id)
        self._load()
