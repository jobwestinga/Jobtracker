"""
Add / Edit session dialog with sensible datetime defaults
and start < end validation.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QDateTimeEdit, QMessageBox,
)
from PySide6.QtCore import QDateTime, QTime, Qt


class SessionDialog(QDialog):
    def __init__(self, parent=None, session=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Edit Session" if session else "Add Session")
        self.setFixedSize(380, 380)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        # ── Start ────────────────────────────────────────────────────────
        lbl_s = QLabel("Start Time")
        lbl_s.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_s)

        self.start_edit = QDateTimeEdit(self)
        self.start_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setMinimumHeight(34)
        if self.session and self.session.start_time:
            self.start_edit.setDateTime(
                QDateTime.fromString(self.session.start_time[:19], "yyyy-MM-ddTHH:mm:ss")
            )
        else:
            # Default start = 1h ago, but never roll back to the previous day.
            now = QDateTime.currentDateTime()
            start_default = now.addSecs(-3600)
            if start_default.date() != now.date():
                start_default = QDateTime(now.date(), QTime(0, 0, 0))
            self.start_edit.setDateTime(start_default)
        layout.addWidget(self.start_edit)

        # ── End ──────────────────────────────────────────────────────────
        lbl_e = QLabel("End Time")
        lbl_e.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_e)

        self.end_edit = QDateTimeEdit(self)
        self.end_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setMinimumHeight(34)
        if self.session and self.session.end_time:
            self.end_edit.setDateTime(
                QDateTime.fromString(self.session.end_time[:19], "yyyy-MM-ddTHH:mm:ss")
            )
        else:
            self.end_edit.setDateTime(QDateTime.currentDateTime())
        layout.addWidget(self.end_edit)

        # ── Note ─────────────────────────────────────────────────────────
        lbl_n = QLabel("Note")
        lbl_n.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_n)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Optional session note …")
        self.notes_input.setMaximumHeight(70)
        if self.session and self.session.note:
            self.notes_input.setPlainText(self.session.note)
        layout.addWidget(self.notes_input)

        layout.addStretch()

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Save Session")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(36)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._validate_and_accept)
        btn_row.addWidget(save)

        layout.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)

    def _validate_and_accept(self) -> None:
        start = self.start_edit.dateTime()
        end = self.end_edit.dateTime()
        if start >= end:
            QMessageBox.warning(
                self, "Invalid Times",
                "Start time must be before end time.",
            )
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "start_time": self.start_edit.dateTime().toPython(),
            "end_time": self.end_edit.dateTime().toPython(),
            "note": self.notes_input.toPlainText().strip(),
        }
