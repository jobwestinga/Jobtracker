"""
Recurring goal-template dialogs.

A template appends a normal goal instance to the top of the list once per logical
period (daily/weekly/monthly). Generated goals are ordinary editable/completable
goals — there is no separate "routine" object in the main list.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

RECURRENCE_LABELS = [("Daily", "daily"), ("Weekly", "weekly"), ("Monthly", "monthly")]


class TemplateDialog(QDialog):
    def __init__(self, parent=None, template=None) -> None:
        super().__init__(parent)
        self.template = template
        self.setWindowTitle("Edit Template" if template else "New Template")
        self.setMinimumWidth(420)
        self._milestone_rows: list[QLineEdit] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        layout.addWidget(self._lbl("Title"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Daily routine pack")
        self.title_input.setMinimumHeight(34)
        layout.addWidget(self.title_input)

        layout.addWidget(self._lbl("Description"))
        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(70)
        layout.addWidget(self.desc_input)

        layout.addWidget(self._lbl("Repeats"))
        self.recurrence_combo = QComboBox()
        for label, value in RECURRENCE_LABELS:
            self.recurrence_combo.addItem(label, value)
        layout.addWidget(self.recurrence_combo)

        layout.addWidget(self._lbl("Milestones copied into each generated goal"))
        self._ms_container = QVBoxLayout()
        self._ms_container.setSpacing(4)
        layout.addLayout(self._ms_container)
        add_btn = QPushButton("+ Add milestone")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_milestone_row)
        layout.addWidget(add_btn)

        # Prefill (edit).
        if self.template:
            self.title_input.setText(self.template.title)
            self.desc_input.setPlainText(self.template.notes or "")
            idx = self.recurrence_combo.findData(self.template.recurrence)
            if idx >= 0:
                self.recurrence_combo.setCurrentIndex(idx)
            try:
                for entry in json.loads(self.template.milestones_json or "[]"):
                    title = entry.get("title", "") if isinstance(entry, dict) else str(entry)
                    self._add_milestone_row(title)
            except (ValueError, TypeError):
                pass
        if not self._milestone_rows:
            self._add_milestone_row()

        layout.addStretch()

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Save Template")
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

    def _add_milestone_row(self, text: str = "") -> None:
        row = QLineEdit()
        row.setPlaceholderText("Milestone title …")
        row.setMinimumHeight(30)
        if text:
            row.setText(text)
        self._ms_container.addWidget(row)
        self._milestone_rows.append(row)

    def _accept(self) -> None:
        if not self.title_input.text().strip():
            QMessageBox.warning(self, "Validation", "Template title cannot be empty.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "title": self.title_input.text().strip(),
            "notes": self.desc_input.toPlainText().strip(),
            "recurrence": self.recurrence_combo.currentData(),
            "milestones": [r.text().strip() for r in self._milestone_rows if r.text().strip()],
        }


class TemplateManagerDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Recurring Templates")
        self.setMinimumSize(460, 420)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        info = QLabel(
            "Templates add a fresh goal to the top of your list each new period."
        )
        info.setWordWrap(True)
        info.setStyleSheet("opacity: 0.8;")
        layout.addWidget(info)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.setObjectName("primaryBtn")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._new)
        row.addWidget(new_btn)
        edit_btn = QPushButton("Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(self._edit)
        row.addWidget(edit_btn)
        toggle_btn = QPushButton("Enable/Disable")
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.clicked.connect(self._toggle_active)
        row.addWidget(toggle_btn)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("dangerBtn")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._delete)
        row.addWidget(del_btn)
        layout.addLayout(row)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _reload(self) -> None:
        self._list.clear()
        for tpl in self.service.get_goal_templates():
            state = "" if tpl.is_active else "  (disabled)"
            item = QListWidgetItem(f"{tpl.title}  ·  {tpl.recurrence}{state}")
            item.setData(Qt.UserRole, tpl.id)
            self._list.addItem(item)

    def _selected_id(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _new(self) -> None:
        dlg = TemplateDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            self.service.add_goal_template(d["title"], d["notes"], d["recurrence"], d["milestones"])
            self._reload()

    def _edit(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        tpl = next((t for t in self.service.get_goal_templates() if t.id == tid), None)
        if not tpl:
            return
        dlg = TemplateDialog(self, template=tpl)
        if dlg.exec():
            d = dlg.get_data()
            self.service.update_goal_template(tid, d["title"], d["notes"], d["recurrence"], d["milestones"])
            self._reload()

    def _toggle_active(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        tpl = next((t for t in self.service.get_goal_templates() if t.id == tid), None)
        if tpl:
            self.service.set_goal_template_active(tid, not tpl.is_active)
            self._reload()

    def _delete(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        if QMessageBox.question(
            self, "Delete Template",
            "Delete this template? Already-generated goals are kept.",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.service.delete_goal_template(tid)
            self._reload()
