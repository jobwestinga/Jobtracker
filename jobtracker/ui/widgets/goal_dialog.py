"""
Goal dialogs (the redesigned Tasks tab).

- GoalDialog: create/edit a goal's title + description. On CREATE it can also
  seed an initial milestone list.
- GoalDetailDialog: the focused view — description, milestone checklist with
  progress, add/check/uncheck/delete milestones, and milestone-gated completion
  (a goal can only be completed when all milestones are done, or it has none).
  Completion is always reversible (Reopen).

These are thin shells over TrackerService; all rules live in the service.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)


class GoalDialog(QDialog):
    """Create or edit a goal's title + description (+ initial milestones on new)."""

    def __init__(self, parent=None, goal=None) -> None:
        super().__init__(parent)
        self.goal = goal
        self._is_edit = goal is not None
        self.setWindowTitle("Edit Goal" if self._is_edit else "New Goal")
        self.setMinimumWidth(420)
        self._milestone_rows: list[QLineEdit] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        layout.addWidget(self._lbl("Title"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Become a FIDE master")
        self.title_input.setMinimumHeight(34)
        if self.goal:
            self.title_input.setText(self.goal.name)
        layout.addWidget(self.title_input)

        layout.addWidget(self._lbl("Description / motivation"))
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Why this matters, the outcome you want …")
        self.desc_input.setMaximumHeight(90)
        if self.goal and self.goal.notes:
            self.desc_input.setPlainText(self.goal.notes)
        layout.addWidget(self.desc_input)

        # Initial milestones (create only; edit manages them in the detail view).
        if not self._is_edit:
            layout.addWidget(self._lbl("Milestones (optional)"))
            self._ms_container = QVBoxLayout()
            self._ms_container.setSpacing(4)
            layout.addLayout(self._ms_container)
            self._add_milestone_row()
            add_btn = QPushButton("+ Add milestone")
            add_btn.setCursor(Qt.PointingHandCursor)
            add_btn.clicked.connect(self._add_milestone_row)
            layout.addWidget(add_btn)

        layout.addStretch()

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Save Goal")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(36)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600;")
        return lbl

    def _add_milestone_row(self) -> None:
        row = QLineEdit()
        row.setPlaceholderText("Milestone title …")
        row.setMinimumHeight(30)
        self._ms_container.addWidget(row)
        self._milestone_rows.append(row)

    def _accept(self) -> None:
        if not self.title_input.text().strip():
            QMessageBox.warning(self, "Validation", "Goal title cannot be empty.")
            return
        self.accept()

    def get_data(self) -> dict:
        milestones = []
        if not self._is_edit:
            milestones = [r.text().strip() for r in self._milestone_rows if r.text().strip()]
        return {
            "name": self.title_input.text().strip(),
            "notes": self.desc_input.toPlainText().strip(),
            "milestones": milestones,
        }


class GoalDetailDialog(QDialog):
    """Focused goal view: milestones checklist, progress, completion."""

    def __init__(self, service, goal_id: int, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.goal_id = goal_id
        self.setWindowTitle("Goal")
        self.setMinimumSize(460, 540)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: 800;")
        self.title_lbl.setWordWrap(True)
        layout.addWidget(self.title_lbl)

        self.desc_lbl = QLabel("")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet("font-size: 12px; opacity: 0.85;")
        layout.addWidget(self.desc_lbl)

        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("font-size: 12px; font-weight: 700;")
        layout.addWidget(self.progress_lbl)

        # Scrollable milestone checklist.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._ms_host = QWidget()
        self._ms_layout = QVBoxLayout(self._ms_host)
        self._ms_layout.setContentsMargins(0, 0, 0, 0)
        self._ms_layout.setSpacing(4)
        self._ms_layout.addStretch()
        scroll.setWidget(self._ms_host)
        layout.addWidget(scroll, 1)

        # Add-milestone row.
        add_row = QHBoxLayout()
        self.new_ms_input = QLineEdit()
        self.new_ms_input.setPlaceholderText("Add a milestone …")
        self.new_ms_input.setMinimumHeight(30)
        self.new_ms_input.returnPressed.connect(self._add_milestone)
        add_row.addWidget(self.new_ms_input, 1)
        add_btn = QPushButton("Add")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_milestone)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        # Actions.
        btn_row = QHBoxLayout()
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self._edit_goal)
        btn_row.addWidget(self.edit_btn)
        btn_row.addStretch()
        self.complete_btn = QPushButton("Mark Complete")
        self.complete_btn.setObjectName("primaryBtn")
        self.complete_btn.setMinimumHeight(34)
        self.complete_btn.setCursor(Qt.PointingHandCursor)
        self.complete_btn.clicked.connect(self._toggle_complete)
        btn_row.addWidget(self.complete_btn)
        layout.addLayout(btn_row)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _reload(self) -> None:
        goal = self.service.get_goal(self.goal_id)
        if goal is None:
            self.accept()
            return
        self._goal = goal
        self.title_lbl.setText(goal.name)
        self.desc_lbl.setText(goal.notes or "No description.")

        # Rebuild milestone rows.
        while self._ms_layout.count() > 1:  # keep trailing stretch
            item = self._ms_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        milestones = self.service.get_goal_milestones(self.goal_id)
        for ms in milestones:
            self._ms_layout.insertWidget(self._ms_layout.count() - 1, self._milestone_row(ms))

        done, total = self.service.get_goal_progress(self.goal_id)
        if total:
            self.progress_lbl.setText(f"Progress: {done}/{total} milestones")
        else:
            self.progress_lbl.setText("No milestones — complete manually")

        is_completed = bool(goal.is_completed)
        if is_completed:
            self.complete_btn.setText("Reopen")
            self.complete_btn.setEnabled(True)
        else:
            self.complete_btn.setText("Mark Complete")
            self.complete_btn.setEnabled(self.service.can_complete_goal(self.goal_id))

    def _milestone_row(self, ms) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        check = QCheckBox(ms.title)
        check.setChecked(bool(ms.is_done))
        check.setCursor(Qt.PointingHandCursor)
        check.toggled.connect(lambda checked, mid=ms.id: self._toggle_milestone(mid, checked))
        lay.addWidget(check, 1)
        if ms.note:
            note = QLabel(ms.note)
            note.setStyleSheet("font-size: 10px; opacity: 0.6;")
            lay.addWidget(note)
        delete = QPushButton("✕")
        delete.setFixedSize(24, 24)
        delete.setCursor(Qt.PointingHandCursor)
        delete.setToolTip("Delete milestone")
        delete.clicked.connect(lambda _=False, mid=ms.id: self._delete_milestone(mid))
        lay.addWidget(delete)
        return row

    def _toggle_milestone(self, milestone_id: int, checked: bool) -> None:
        self.service.set_milestone_done(milestone_id, checked)
        self._reload()

    def _add_milestone(self) -> None:
        title = self.new_ms_input.text().strip()
        if not title:
            return
        self.service.add_milestone(self.goal_id, title)
        self.new_ms_input.clear()
        self._reload()

    def _delete_milestone(self, milestone_id: int) -> None:
        self.service.delete_milestone(milestone_id)
        self._reload()

    def _edit_goal(self) -> None:
        dlg = GoalDialog(self, goal=self._goal)
        if dlg.exec():
            d = dlg.get_data()
            self.service.update_todo_task(self.goal_id, d["name"], d["notes"], None)
            self._reload()

    def _toggle_complete(self) -> None:
        goal = self.service.get_goal(self.goal_id)
        if goal and goal.is_completed:
            self.service.uncomplete_goal(self.goal_id)
            self._reload()
            return
        if not self.service.complete_goal(self.goal_id):
            QMessageBox.information(
                self, "Milestones remain",
                "Finish all milestones before completing this goal "
                "(or remove them).",
            )
            return
        self.accept()
