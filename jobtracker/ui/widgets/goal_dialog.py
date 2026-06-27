"""Polished Goal create/edit and focused milestone-checklist dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def _tokens_from(parent, explicit: dict | None = None) -> dict:
    if explicit:
        return explicit
    current = parent
    while current is not None:
        tokens = getattr(current, "_tokens", None)
        if tokens:
            return tokens
        current = current.parent() if hasattr(current, "parent") else None
    return {
        "CARD_BG": "#131D2E",
        "BG_SECONDARY": "#131D2E",
        "BG_TERTIARY": "#18243A",
        "BORDER_COLOR": "#263852",
        "TEXT_PRIMARY": "#E2E8F0",
        "TEXT_SECONDARY": "#94A3B8",
        "TEXT_DIMMED": "#64748B",
        "ACCENT": "#3B82F6",
        "ACCENT_GREEN": "#22C55E",
    }


def apply_goal_edits(service, goal_id: int, data: dict) -> None:
    """Apply one edit-dialog result, including milestone add/edit/delete."""
    service.update_todo_task(goal_id, data["name"], data["notes"], None)
    existing = {
        milestone.id: milestone
        for milestone in service.get_goal_milestones(goal_id)
        if milestone.id is not None
    }
    submitted_ids = {
        entry["id"] for entry in data["milestones"] if entry.get("id") is not None
    }
    for milestone_id in existing.keys() - submitted_ids:
        service.delete_milestone(milestone_id)
    for entry in data["milestones"]:
        milestone_id = entry.get("id")
        if milestone_id is None:
            service.add_milestone(goal_id, entry["title"])
        else:
            service.update_milestone(
                milestone_id,
                entry["title"],
                existing[milestone_id].note,
            )


class GoalDialog(QDialog):
    """Create/edit the Goal and its complete milestone definition."""

    def __init__(
        self,
        parent=None,
        goal=None,
        milestones=None,
        tokens: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.goal = goal
        self._is_edit = goal is not None
        self._tokens = _tokens_from(parent, tokens)
        self._initial_milestones = list(milestones or [])
        self._milestone_rows: list[dict] = []
        self.setWindowTitle("Edit Goal" if self._is_edit else "New Goal")
        self.setMinimumSize(500, 580)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        heading = QLabel("Edit the outcome" if self._is_edit else "Define a new outcome")
        heading.setStyleSheet("font-size: 18px; font-weight: 800;")
        layout.addWidget(heading)

        layout.addWidget(self._label("Title"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Become a FIDE master")
        self.title_input.setMinimumHeight(38)
        if self.goal:
            self.title_input.setText(self.goal.name)
        layout.addWidget(self.title_input)

        layout.addWidget(self._label("Description / motivation"))
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Why this matters and what success looks like …")
        self.desc_input.setMaximumHeight(100)
        if self.goal and self.goal.notes:
            self.desc_input.setPlainText(self.goal.notes)
        layout.addWidget(self.desc_input)

        milestone_header = QHBoxLayout()
        milestone_header.addWidget(self._label("Milestones"))
        milestone_header.addStretch()
        self._add_milestone_btn = QPushButton("+ Add milestone")
        self._add_milestone_btn.setCursor(Qt.PointingHandCursor)
        self._add_milestone_btn.clicked.connect(
            lambda _checked=False: self._add_milestone_row()
        )
        milestone_header.addWidget(self._add_milestone_btn)
        layout.addLayout(milestone_header)

        milestone_hint = QLabel(
            "Add, rename, or remove milestones here. The Goal view is only for checking them off."
        )
        milestone_hint.setWordWrap(True)
        milestone_hint.setStyleSheet(
            f"font-size: 11px; color: {self._tokens['TEXT_DIMMED']};"
        )
        layout.addWidget(milestone_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._ms_container = QVBoxLayout(host)
        self._ms_container.setContentsMargins(0, 2, 0, 2)
        self._ms_container.setSpacing(7)
        self._ms_container.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        if self._initial_milestones:
            for milestone in self._initial_milestones:
                self._add_milestone_row(milestone.title, milestone.id)
        else:
            self._add_milestone_row()

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()
        save = QPushButton("Save Goal")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(38)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 700;")
        return label

    def _add_milestone_row(self, text: str = "", milestone_id: int | None = None) -> None:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {self._tokens.get('BG_TERTIARY', '#18243A')};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']}; border-radius: 9px; }}"
        )
        row_layout = QHBoxLayout(frame)
        row_layout.setContentsMargins(10, 7, 8, 7)
        row_layout.setSpacing(8)

        title_input = QLineEdit()
        title_input.setPlaceholderText("Milestone title …")
        title_input.setMinimumHeight(32)
        title_input.setText(text)
        row_layout.addWidget(title_input, 1)

        remove_btn = QPushButton("Remove")
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.setMinimumHeight(30)
        remove_btn.setProperty("no_drag", True)
        row_layout.addWidget(remove_btn)

        entry = {"id": milestone_id, "input": title_input, "frame": frame}
        remove_btn.clicked.connect(lambda: self._remove_milestone_row(entry))
        self._milestone_rows.append(entry)
        self._ms_container.insertWidget(self._ms_container.count() - 1, frame)
        title_input.setFocus()

    def _remove_milestone_row(self, entry: dict) -> None:
        if entry not in self._milestone_rows:
            return
        self._milestone_rows.remove(entry)
        entry["frame"].deleteLater()

    def _accept(self) -> None:
        if not self.title_input.text().strip():
            QMessageBox.warning(self, "Validation", "Goal title cannot be empty.")
            return
        self.accept()

    def get_data(self) -> dict:
        milestones = []
        for entry in self._milestone_rows:
            title = entry["input"].text().strip()
            if title:
                milestones.append({"id": entry["id"], "title": title})
        return {
            "name": self.title_input.text().strip(),
            "notes": self.desc_input.toPlainText().strip(),
            "milestones": milestones,
        }


class GoalDetailDialog(QDialog):
    """Focused, read-only definition with a polished milestone checklist."""

    def __init__(
        self,
        service,
        goal_id: int,
        parent=None,
        tokens: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.goal_id = goal_id
        self._tokens = _tokens_from(parent, tokens)
        self.setWindowTitle("Goal")
        self.setMinimumSize(520, 600)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(12)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-size: 21px; font-weight: 800;")
        self.title_lbl.setWordWrap(True)
        layout.addWidget(self.title_lbl)

        description_frame = QFrame()
        description_frame.setStyleSheet(
            f"QFrame {{ background: {self._tokens.get('CARD_BG', self._tokens['BG_SECONDARY'])};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']}; border-radius: 11px; }}"
        )
        description_layout = QVBoxLayout(description_frame)
        description_layout.setContentsMargins(14, 12, 14, 12)
        description_caption = QLabel("MOTIVATION")
        description_caption.setStyleSheet(
            f"font-size: 9px; font-weight: 800; letter-spacing: 1px;"
            f" color: {self._tokens['TEXT_DIMMED']};"
            " border: none; background: transparent;"
        )
        description_layout.addWidget(description_caption)
        self.desc_lbl = QLabel("")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(
            f"font-size: 12px; color: {self._tokens['TEXT_SECONDARY']};"
            " border: none; background: transparent;"
        )
        description_layout.addWidget(self.desc_lbl)
        layout.addWidget(description_frame)

        progress_row = QHBoxLayout()
        milestones_title = QLabel("Milestones")
        milestones_title.setStyleSheet("font-size: 14px; font-weight: 750;")
        progress_row.addWidget(milestones_title)
        progress_row.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 750; color: {self._tokens['ACCENT']};"
            f" background: {self._tokens.get('BG_TERTIARY', '#18243A')};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']};"
            " border-radius: 9px; padding: 4px 9px;"
        )
        progress_row.addWidget(self.progress_lbl)
        layout.addLayout(progress_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._ms_host = QWidget()
        self._ms_host.setStyleSheet("background: transparent;")
        self._ms_layout = QVBoxLayout(self._ms_host)
        self._ms_layout.setContentsMargins(0, 2, 0, 2)
        self._ms_layout.setSpacing(8)
        self._ms_layout.addStretch()
        scroll.setWidget(self._ms_host)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(38)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        self.edit_btn = QPushButton("Edit Goal")
        self.edit_btn.setMinimumHeight(38)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self._edit_goal)
        btn_row.addWidget(self.edit_btn)
        self.complete_btn = QPushButton("Mark Complete")
        self.complete_btn.setObjectName("primaryBtn")
        self.complete_btn.setMinimumHeight(38)
        self.complete_btn.setCursor(Qt.PointingHandCursor)
        self.complete_btn.clicked.connect(self._toggle_complete)
        btn_row.addWidget(self.complete_btn)
        layout.addLayout(btn_row)

    def _reload(self) -> None:
        goal = self.service.get_goal(self.goal_id)
        if goal is None:
            self.accept()
            return
        self._goal = goal
        self.title_lbl.setText(goal.name)
        self.desc_lbl.setText(goal.notes or "No description or motivation yet.")

        while self._ms_layout.count() > 1:
            item = self._ms_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        milestones = self.service.get_goal_milestones(self.goal_id)
        if milestones:
            for milestone in milestones:
                self._ms_layout.insertWidget(
                    self._ms_layout.count() - 1,
                    self._milestone_row(milestone),
                )
        else:
            empty = QLabel("No milestones yet. Use Edit Goal to add them.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {self._tokens['TEXT_DIMMED']}; font-style: italic; padding: 28px;"
            )
            self._ms_layout.insertWidget(0, empty)

        done, total = self.service.get_goal_progress(self.goal_id)
        self.progress_lbl.setText(f"{done} / {total} complete" if total else "No milestones")

        if goal.is_completed:
            self.complete_btn.setText("Restore to Active")
            self.complete_btn.setEnabled(True)
        else:
            self.complete_btn.setText("Archive Goal")
            self.complete_btn.setEnabled(self.service.can_complete_goal(self.goal_id))

    def _milestone_row(self, milestone) -> QFrame:
        frame = QFrame()
        frame.setMinimumHeight(52)
        frame.setStyleSheet(
            f"QFrame {{ background: {self._tokens.get('CARD_BG', self._tokens['BG_SECONDARY'])};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']}; border-radius: 11px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(13, 9, 13, 9)
        layout.setSpacing(3)

        check = QCheckBox(milestone.title)
        check.setChecked(bool(milestone.is_done))
        check.setEnabled(not self._goal.is_completed)
        check.setCursor(
            Qt.ArrowCursor if self._goal.is_completed else Qt.PointingHandCursor
        )
        check.setStyleSheet(
            f"QCheckBox {{ color: {self._tokens['TEXT_PRIMARY']}; font-size: 13px;"
            " font-weight: 650; spacing: 10px; background: transparent; }"
            f"QCheckBox::indicator {{ width: 18px; height: 18px;"
            f" border: 2px solid {self._tokens['TEXT_DIMMED']};"
            " border-radius: 10px; background: transparent; }"
            f"QCheckBox::indicator:hover {{ border-color: {self._tokens['ACCENT_GREEN']}; }}"
            f"QCheckBox::indicator:checked {{ background: {self._tokens['ACCENT_GREEN']};"
            f" border-color: {self._tokens['ACCENT_GREEN']}; }}"
        )
        if milestone.is_done:
            font = QFont(check.font())
            font.setStrikeOut(True)
            check.setFont(font)
        check.toggled.connect(
            lambda checked, milestone_id=milestone.id: self._toggle_milestone(
                milestone_id, checked
            )
        )
        layout.addWidget(check)

        if milestone.note:
            note = QLabel(milestone.note)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"font-size: 10px; color: {self._tokens['TEXT_DIMMED']};"
                " padding-left: 30px; background: transparent;"
            )
            layout.addWidget(note)
        return frame

    def _toggle_milestone(self, milestone_id: int, checked: bool) -> None:
        self.service.set_milestone_done(milestone_id, checked)
        self._reload()

    def _edit_goal(self) -> None:
        milestones = self.service.get_goal_milestones(self.goal_id)
        dialog = GoalDialog(
            self,
            goal=self._goal,
            milestones=milestones,
            tokens=self._tokens,
        )
        if dialog.exec():
            apply_goal_edits(self.service, self.goal_id, dialog.get_data())
            self._reload()

    def _toggle_complete(self) -> None:
        goal = self.service.get_goal(self.goal_id)
        if goal and goal.is_completed:
            self.service.uncomplete_goal(self.goal_id)
            self._reload()
            return
        if not self.service.complete_goal(self.goal_id):
            QMessageBox.information(
                self,
                "Milestones remain",
                "Finish all milestones before archiving this goal.",
            )
            return
        self.accept()
