"""
Dialog for creating or editing a completable task.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ...core.models import TodoTask
from .dialog_utils import InlineDialog, configure_window_modal


class TodoTaskDialog(InlineDialog):
    def __init__(self, parent=None, todo_task: TodoTask | None = None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.todo_task = todo_task
        self.setWindowTitle("Edit Task" if todo_task else "New Task")
        self.setFixedSize(420, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        name_lbl = QLabel("Task Name")
        name_lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(name_lbl)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Submit report, Read chapter 5 …")
        self.name_input.setMinimumHeight(36)
        if self.todo_task:
            self.name_input.setText(self.todo_task.name)
        layout.addWidget(self.name_input)

        notes_lbl = QLabel("Description")
        notes_lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(notes_lbl)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Optional details …")
        self.notes_input.setMaximumHeight(100)
        if self.todo_task and self.todo_task.notes:
            self.notes_input.setPlainText(self.todo_task.notes)
        layout.addWidget(self.notes_input)

        self.deadline_check = QCheckBox("Has deadline")
        self.deadline_check.setCursor(Qt.PointingHandCursor)
        self.deadline_check.toggled.connect(self._toggle_deadline)
        layout.addWidget(self.deadline_check)

        self.deadline_edit = QDateTimeEdit(self)
        self.deadline_edit.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setMinimumHeight(34)
        self.deadline_edit.setDateTime(QDateTime.currentDateTime().addDays(1))
        layout.addWidget(self.deadline_edit)

        if self.todo_task and self.todo_task.deadline:
            dt = QDateTime.fromString(self.todo_task.deadline[:19], "yyyy-MM-ddTHH:mm:ss")
            if dt.isValid():
                self.deadline_edit.setDateTime(dt)
            self.deadline_check.setChecked(True)
        else:
            self.deadline_check.setChecked(False)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save Task")
        save_btn.setObjectName("primaryBtn")
        save_btn.setMinimumHeight(36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save_btn.setAutoDefault(True)
        save_btn.setDefault(True)

    def _toggle_deadline(self, enabled: bool) -> None:
        self.deadline_edit.setEnabled(enabled)

    def get_data(self) -> dict:
        deadline = None
        if self.deadline_check.isChecked():
            deadline = self.deadline_edit.dateTime().toPython().isoformat()
        return {
            "name": self.name_input.text().strip(),
            "notes": self.notes_input.toPlainText().strip(),
            "deadline": deadline,
        }
