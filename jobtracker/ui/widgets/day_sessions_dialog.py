"""
Sessions for a single logical day (opened from a heatmap cell).

Read-only list first; "Edit in Subject…" opens the existing per-subject session
manager for the selected session, reusing all existing edit/add/delete UI.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from .manage_sessions_dialog import ManageSessionsDialog
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    information,
    open_dialog,
)


def _fmt(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


class DaySessionsDialog(InlineDialog):
    def __init__(self, service, day: date, parent=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.service = service
        self.day = day
        self.setWindowTitle(f"Sessions · {day.isoformat()}")
        self.setMinimumSize(440, 380)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        self._total_lbl = QLabel("")
        self._total_lbl.setStyleSheet("font-weight: 700;")
        layout.addWidget(self._total_lbl)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        edit_btn = QPushButton("Edit in Subject…")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(self._edit_selected)
        row.addWidget(edit_btn)
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _reload(self) -> None:
        self._list.clear()
        sessions = self.service.get_sessions_for_logical_day(self.day)
        total = sum(s.get("duration_seconds", 0) for s in sessions)
        self._total_lbl.setText(
            f"{self.day.isoformat()} — {_fmt(total)} across {len(sessions)} session(s)"
        )
        for s in sorted(sessions, key=lambda x: x.get("start_time", "")):
            try:
                start = datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                end = datetime.fromisoformat(s["end_time"]).strftime("%H:%M") if s.get("end_time") else "…"
            except (ValueError, KeyError):
                start, end = "?", "?"
            note = f"  —  {s['note']}" if s.get("note") else ""
            item = QListWidgetItem(
                f"{s['subject_name']}   {start}–{end}   ·   {_fmt(s.get('duration_seconds', 0))}{note}"
            )
            item.setData(Qt.UserRole, s.get("subject_id"))
            self._list.addItem(item)

    def _edit_selected(self) -> None:
        item = self._list.currentItem()
        if not item:
            information(self, "No selection", "Pick a session to edit.")
            return
        subject_id = item.data(Qt.UserRole)
        if subject_id is None:
            return
        # Reuse the existing per-subject session manager for full edit support.
        dialog = ManageSessionsDialog(subject_id, self.service, self)
        open_dialog(
            dialog,
            lambda _result, _dialog: self._reload(),
        )
