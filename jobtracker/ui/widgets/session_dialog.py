"""
Add / Edit session dialog with sensible datetime defaults
and start < end validation.

When editing an existing session (and a service is supplied), a Subject
dropdown lets the session be moved to another subject: same times, same
duration, same note — only the owning subject changes.
"""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QDateTimeEdit,
)
from PySide6.QtCore import QDateTime, QTime, Qt

from .dialog_utils import InlineDialog, configure_window_modal, warning


def apply_session_edits(service, session, data: dict):
    """Persist a :class:`SessionDialog` result onto an existing session.

    Falls back to the session's current subject when the dialog had no subject
    picker, so an edit can never strand a session on a missing subject.
    """
    subject_id = data.get("subject_id") or session.subject_id
    return service.update_session(
        session.id,
        subject_id,
        data["start_time"],
        data["end_time"],
        data["note"],
    )


class SessionDialog(InlineDialog):
    def __init__(
        self,
        parent=None,
        session=None,
        service=None,
        current_subject_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.session = session
        self._service = service
        self._current_subject_id = current_subject_id
        self.subject_combo: QComboBox | None = None
        self.setWindowTitle("Edit Session" if session else "Add Session")
        show_subject_picker = session is not None and service is not None
        self.setFixedSize(380, 448 if show_subject_picker else 380)
        self._build_ui(show_subject_picker)

    def _build_ui(self, show_subject_picker: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        # ── Subject (edit only): move the session to another subject ─────
        if show_subject_picker:
            lbl_subject = QLabel("Subject")
            lbl_subject.setStyleSheet("font-weight: 600;")
            layout.addWidget(lbl_subject)

            self.subject_combo = QComboBox(self)
            self.subject_combo.setMinimumHeight(34)
            self.subject_combo.setCursor(Qt.PointingHandCursor)
            subjects = list(self._service.get_all_subjects())
            # The session's own subject must always be present (it may be
            # archived); keep it selectable so editing never strands a session.
            if self._current_subject_id is not None and all(
                s.id != self._current_subject_id for s in subjects
            ):
                current = next(
                    (
                        s
                        for s in self._service.get_all_subjects_including_archived()
                        if s.id == self._current_subject_id
                    ),
                    None,
                )
                if current is not None:
                    subjects.insert(0, current)
            for subject in subjects:
                if subject.id is None:
                    continue
                self.subject_combo.addItem(subject.name, subject.id)
            index = self.subject_combo.findData(self._current_subject_id)
            if index >= 0:
                self.subject_combo.setCurrentIndex(index)
            layout.addWidget(self.subject_combo)

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
            warning(
                self, "Invalid Times",
                "Start time must be before end time.",
            )
            return
        self.accept()

    def get_data(self) -> dict:
        subject_id = None
        if self.subject_combo is not None:
            subject_id = self.subject_combo.currentData()
        return {
            "subject_id": subject_id,
            "start_time": self.start_edit.dateTime().toPython(),
            "end_time": self.end_edit.dateTime().toPython(),
            "note": self.notes_input.toPlainText().strip(),
        }
