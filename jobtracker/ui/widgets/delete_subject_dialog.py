"""
Strong delete-confirmation dialog for subjects that have tracked sessions.

Deleting a subject cascades and permanently removes its sessions. When there's
history to lose, this dialog spells out the consequences, recommends archiving
instead, and requires the user to type the subject name (or DELETE) to confirm.
Subjects with no sessions use a simple confirm elsewhere — this is not shown.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPushButton, QVBoxLayout,
)

from .dialog_utils import InlineDialog, configure_window_modal

# Custom result codes.
RESULT_CANCEL = 0
RESULT_DELETE = 1
RESULT_ARCHIVE = 2


def _fmt_hours(seconds: int) -> str:
    return f"{max(0, int(seconds)) / 3600:.1f}h"


class DeleteSubjectDialog(InlineDialog):
    def __init__(self, subject_name: str, summary: dict, parent=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self._name = subject_name
        self._summary = summary
        self.setWindowTitle("Delete Subject")
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self) -> None:
        s = self._summary
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        warn = QLabel(f"Permanently delete “{self._name}”?")
        warn.setStyleSheet("font-size: 15px; font-weight: 700;")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        earliest = (s.get("earliest") or "")[:10]
        latest = (s.get("latest") or "")[:10]
        date_range = f"{earliest} → {latest}" if earliest else "—"
        facts = QLabel(
            f"This will also delete {s['session_count']} session(s), "
            f"{_fmt_hours(s['total_seconds'])} of tracked time.\n"
            f"Date range: {date_range}\n\n"
            f"This cannot be undone. Archiving keeps the history and just hides "
            f"the subject — that's usually what you want."
        )
        facts.setWordWrap(True)
        layout.addWidget(facts)

        # Preview of the sessions that would be lost.
        sessions = s.get("sessions", [])
        if sessions:
            preview = QListWidget()
            preview.setMaximumHeight(120)
            for sess in sessions[:8]:
                h, rem = divmod(int(sess.duration_seconds), 3600)
                m, _ = divmod(rem, 60)
                dur = f"{h}h {m}m" if h else f"{m}m"
                start = (sess.start_time or "")[:16].replace("T", "  ")
                preview.addItem(f"{start}   ·   {dur}")
            if len(sessions) > 8:
                preview.addItem(f"… and {len(sessions) - 8} more")
            layout.addWidget(preview)

        confirm_lbl = QLabel(
            f"To confirm, type the subject name (<b>{self._name}</b>) or <b>DELETE</b>:"
        )
        confirm_lbl.setTextFormat(Qt.RichText)
        layout.addWidget(confirm_lbl)

        self._confirm_input = QLineEdit()
        self._confirm_input.setPlaceholderText(self._name)
        self._confirm_input.textChanged.connect(self._update_delete_enabled)
        layout.addWidget(self._confirm_input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(lambda: self.done(RESULT_CANCEL))
        btn_row.addWidget(cancel)

        archive = QPushButton("Archive Instead")
        archive.setObjectName("primaryBtn")
        archive.setMinimumHeight(36)
        archive.setCursor(Qt.PointingHandCursor)
        archive.clicked.connect(lambda: self.done(RESULT_ARCHIVE))
        btn_row.addWidget(archive)

        self._delete_btn = QPushButton("Delete Permanently")
        self._delete_btn.setObjectName("dangerBtn")
        self._delete_btn.setMinimumHeight(36)
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(lambda: self.done(RESULT_DELETE))
        btn_row.addWidget(self._delete_btn)

        layout.addLayout(btn_row)

    def _update_delete_enabled(self, text: str) -> None:
        value = text.strip()
        self._delete_btn.setEnabled(
            value == self._name or value == "DELETE"
        )
