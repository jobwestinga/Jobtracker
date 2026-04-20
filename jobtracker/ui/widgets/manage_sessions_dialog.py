"""
Manage Sessions dialog — lists all closed sessions for a given subject
and allows add / edit / delete, plus quick-add buttons.
"""

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt
from ...services.tracker_service import TrackerService
from .session_dialog import SessionDialog


class ManageSessionsDialog(QDialog):
    def __init__(self, subject_id: int, service: TrackerService, parent=None) -> None:
        super().__init__(parent)
        self.subject_id = subject_id
        self.service = service

        subject = next((s for s in service.get_all_subjects() if s.id == subject_id), None)
        title = f"Sessions — {subject.name}" if subject else "Sessions"
        self.setWindowTitle(title)
        self.setMinimumSize(440, 560)
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
        layout.addWidget(self._list)

        # ── Quick-add buttons ────────────────────────────────────────────
        quick_label = QLabel("Quick Add (ending now):")
        quick_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(quick_label)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)

        for label, minutes in [("15 min", 15), ("30 min", 30), ("1 hour", 60), ("1h 15m", 75), ("2 hours", 120)]:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setObjectName("primaryBtn")
            btn.setStyleSheet("font-size: 12px; padding: 6px 12px;")
            btn.clicked.connect(lambda checked, m=minutes: self._quick_add(m))
            quick_row.addWidget(btn)

        layout.addLayout(quick_row)

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
    def _load(self) -> None:
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
            m, sec = divmod(rem, 60)
            dur = f"{h}h {m}m {sec}s"
            start = s.start_time[:16].replace("T", "  ")
            line = f"{start}   ·   {dur}"
            if s.note:
                line += f"\n  {s.note}"

            item = QListWidgetItem(line)
            item.setData(Qt.UserRole, s)
            self._list.addItem(item)

    # ── Actions ──────────────────────────────────────────────────────────
    def _quick_add(self, minutes: int) -> None:
        """Create a session ending now that started *minutes* ago."""
        end = datetime.now()
        start = end - timedelta(minutes=minutes)
        self.service.add_session(self.subject_id, start, end)
        self._load()

    def _add(self) -> None:
        dlg = SessionDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            self.service.add_session(self.subject_id, d["start_time"], d["end_time"], d["note"])
            self._load()

    def _edit(self) -> None:
        sel = self._list.currentItem()
        if not sel:
            QMessageBox.information(self, "No Selection", "Select a session to edit.")
            return
        session = sel.data(Qt.UserRole)
        if session.id is None:
            QMessageBox.warning(self, "Invalid Session", "Selected session has no valid identifier.")
            return
        dlg = SessionDialog(self, session)
        if dlg.exec():
            d = dlg.get_data()
            self.service.update_session(session.id, self.subject_id, d["start_time"], d["end_time"], d["note"])
            self._load()

    def _delete(self) -> None:
        sel = self._list.currentItem()
        if not sel:
            QMessageBox.information(self, "No Selection", "Select a session to delete.")
            return
        session = sel.data(Qt.UserRole)
        if session.id is None:
            QMessageBox.warning(self, "Invalid Session", "Selected session has no valid identifier.")
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            "Delete this session permanently?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.service.delete_session(session.id)
            self._load()
