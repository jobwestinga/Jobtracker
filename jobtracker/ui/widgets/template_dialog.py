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
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QTextEdit,
    QVBoxLayout, QWidget,
)

from ...core.themes import DEFAULT_TOKENS

RECURRENCE_LABELS = [("Daily", "daily"), ("Weekly", "weekly"), ("Monthly", "monthly")]
WEEKDAYS = [
    ("Monday", 1),
    ("Tuesday", 2),
    ("Wednesday", 3),
    ("Thursday", 4),
    ("Friday", 5),
    ("Saturday", 6),
    ("Sunday", 7),
]


def _ordinal(day: int) -> str:
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


class TemplateDialog(QDialog):
    def __init__(self, parent=None, template=None) -> None:
        super().__init__(parent)
        self.template = template
        self._tokens = getattr(parent, "_tokens", DEFAULT_TOKENS)
        self.setWindowTitle("Edit Template" if template else "New Template")
        self.setMinimumSize(500, 650)
        self._milestone_rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        heading = QLabel(
            "Edit the recurring outcome" if self.template else "Define a recurring outcome"
        )
        heading.setStyleSheet("font-size: 18px; font-weight: 800;")
        layout.addWidget(heading)

        layout.addWidget(self._lbl("Title"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Weekly review")
        self.title_input.setMinimumHeight(38)
        layout.addWidget(self.title_input)

        layout.addWidget(self._lbl("Description"))
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText(
            "Why this recurring goal matters and what success looks like …"
        )
        self.desc_input.setMaximumHeight(100)
        layout.addWidget(self.desc_input)

        recurrence_row = QHBoxLayout()
        recurrence_row.setSpacing(8)
        recurrence_row.addWidget(self._lbl("Repeats"))
        self.recurrence_combo = QComboBox()
        for label, value in RECURRENCE_LABELS:
            self.recurrence_combo.addItem(label, value)
        recurrence_row.addWidget(self.recurrence_combo, 1)
        self.schedule_label = QLabel("On")
        self.schedule_label.setStyleSheet("font-weight: 700;")
        recurrence_row.addWidget(self.schedule_label)
        self.schedule_combo = QComboBox()
        recurrence_row.addWidget(self.schedule_combo, 1)
        layout.addLayout(recurrence_row)

        day_start = "03:00"
        service = getattr(self.parent(), "service", None)
        if service is not None:
            day_start = service.get_day_start().strftime("%H:%M")
        self.timing_hint = QLabel(
            f"Daily templates generate when your logical day starts at {day_start}. "
            "If the app is closed, they generate the next time it opens."
        )
        self.timing_hint.setWordWrap(True)
        self.timing_hint.setStyleSheet(
            f"font-size: 11px; color: {self._tokens['TEXT_DIMMED']};"
        )
        layout.addWidget(self.timing_hint)

        milestone_header = QHBoxLayout()
        milestone_header.addWidget(
            self._lbl("Milestones copied into each generated goal")
        )
        milestone_header.addStretch()
        self._add_milestone_btn = QPushButton("+ Add milestone")
        self._add_milestone_btn.setCursor(Qt.PointingHandCursor)
        self._add_milestone_btn.clicked.connect(
            lambda _checked=False: self._add_milestone_row()
        )
        milestone_header.addWidget(self._add_milestone_btn)
        layout.addLayout(milestone_header)

        milestone_hint = QLabel(
            "Each generated goal receives these milestone titles and descriptions."
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

        self.recurrence_combo.currentIndexChanged.connect(
            self._sync_schedule_controls
        )

        # Prefill (edit).
        if self.template:
            self.title_input.setText(self.template.title)
            self.desc_input.setPlainText(self.template.notes or "")
            idx = self.recurrence_combo.findData(self.template.recurrence)
            if idx >= 0:
                self.recurrence_combo.setCurrentIndex(idx)
            try:
                for entry in json.loads(self.template.milestones_json or "[]"):
                    if isinstance(entry, dict):
                        title = entry.get("title", "")
                        note = entry.get("note", "")
                    else:
                        title, note = str(entry), ""
                    self._add_milestone_row(title, note)
            except (ValueError, TypeError):
                pass
        self._sync_schedule_controls()
        if self.template and self.template.recurrence_day is not None:
            index = self.schedule_combo.findData(self.template.recurrence_day)
            if index >= 0:
                self.schedule_combo.setCurrentIndex(index)
        if not self._milestone_rows:
            self._add_milestone_row()

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

        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600;")
        return lbl

    def _sync_schedule_controls(self, _index: int | None = None) -> None:
        recurrence = self.recurrence_combo.currentData()
        previous = self.schedule_combo.currentData()
        self.schedule_combo.clear()
        if recurrence == "weekly":
            for label, value in WEEKDAYS:
                self.schedule_combo.addItem(label, value)
            self.schedule_label.show()
            self.schedule_combo.show()
            self.timing_hint.setText(
                "Generates on the selected weekday at your logical-day start. "
                "If the app is closed, it generates the next time it opens."
            )
        elif recurrence == "monthly":
            for day in range(1, 32):
                self.schedule_combo.addItem(_ordinal(day), day)
            self.schedule_label.show()
            self.schedule_combo.show()
            self.timing_hint.setText(
                "Generates on the selected day at your logical-day start. "
                "For shorter months, dates 29–31 run on the final day."
            )
        else:
            self.schedule_label.hide()
            self.schedule_combo.hide()
            day_start = "03:00"
            service = getattr(self.parent(), "service", None)
            if service is not None:
                day_start = service.get_day_start().strftime("%H:%M")
            self.timing_hint.setText(
                f"Generates when your logical day starts at {day_start}. "
                "If the app is closed, it generates the next time it opens."
            )
            return
        index = self.schedule_combo.findData(previous)
        self.schedule_combo.setCurrentIndex(index if index >= 0 else 0)

    def _add_milestone_row(self, text: str = "", note: str = "") -> None:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {self._tokens.get('BG_TERTIARY', '#18243A')};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']}; border-radius: 9px; }}"
        )
        row_layout = QVBoxLayout(frame)
        row_layout.setContentsMargins(10, 8, 8, 9)
        row_layout.setSpacing(7)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_input = QLineEdit()
        title_input.setPlaceholderText("Milestone title …")
        title_input.setMinimumHeight(32)
        title_input.setText(text)
        title_row.addWidget(title_input, 1)

        remove_btn = QPushButton("Remove")
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.setMinimumHeight(30)
        title_row.addWidget(remove_btn)
        row_layout.addLayout(title_row)

        note_input = QTextEdit()
        note_input.setPlaceholderText("Milestone description (optional) …")
        note_input.setMaximumHeight(58)
        note_input.setPlainText(note or "")
        row_layout.addWidget(note_input)

        entry = {
            "input": title_input,
            "note_input": note_input,
            "frame": frame,
        }
        remove_btn.clicked.connect(
            lambda _checked=False, current=entry: self._remove_milestone_row(current)
        )
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
            QMessageBox.warning(self, "Validation", "Template title cannot be empty.")
            return
        self.accept()

    def get_data(self) -> dict:
        milestones = []
        for entry in self._milestone_rows:
            title = entry["input"].text().strip()
            if title:
                milestones.append(
                    {
                        "title": title,
                        "note": entry["note_input"].toPlainText().strip(),
                    }
                )
        return {
            "title": self.title_input.text().strip(),
            "notes": self.desc_input.toPlainText().strip(),
            "recurrence": self.recurrence_combo.currentData(),
            "recurrence_day": (
                self.schedule_combo.currentData()
                if self.recurrence_combo.currentData() != "daily"
                else None
            ),
            "milestones": milestones,
        }


class TemplateManagerDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._tokens = (getattr(parent, "_tokens", None) if parent is not None else None) or DEFAULT_TOKENS
        self.setWindowTitle("Recurring Templates")
        self.setMinimumSize(500, 460)
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
        accent = self._tokens.get("ACCENT", "#3B82F6")
        border = self._tokens.get("BORDER_COLOR", "#263852")
        card = self._tokens.get("CARD_BG", self._tokens.get("BG_SECONDARY", "#131D2E"))
        text = self._tokens.get("TEXT_PRIMARY", "#E2E8F0")
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: 1px solid {border};"
            " border-radius: 10px; padding: 5px; outline: none; }"
            f"QListWidget::item {{ background: {card}; color: {text};"
            f" border: 1px solid {border}; border-radius: 8px; padding: 10px;"
            " margin: 3px; }"
            f"QListWidget::item:selected {{ border: 2px solid {accent};"
            f" background: {self._tokens.get('BG_TERTIARY', '#18243A')}; }}"
        )
        self._list.currentItemChanged.connect(self._selection_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._edit())
        layout.addWidget(self._list, 1)

        self._selection_label = QLabel("Select a template to edit, disable, or delete it.")
        self._selection_label.setStyleSheet(
            f"font-size: 11px; color: {self._tokens.get('TEXT_DIMMED', '#64748B')};"
        )
        layout.addWidget(self._selection_label)

        row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.setObjectName("primaryBtn")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._new)
        row.addWidget(new_btn)
        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.clicked.connect(self._edit)
        row.addWidget(self._edit_btn)
        self._toggle_btn = QPushButton("Disable")
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_active)
        row.addWidget(self._toggle_btn)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setObjectName("dangerBtn")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.clicked.connect(self._delete)
        row.addWidget(self._delete_btn)
        layout.addLayout(row)

        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _reload(self, selected_id: int | None = None) -> None:
        self._list.clear()
        for tpl in self.service.get_goal_templates():
            state = "Active" if tpl.is_active else "Disabled"
            if tpl.recurrence == "weekly":
                schedule = dict((value, label) for label, value in WEEKDAYS).get(
                    tpl.recurrence_day or 1, "Monday"
                )
                recurrence_label = f"Weekly on {schedule}"
            elif tpl.recurrence == "monthly":
                recurrence_label = f"Monthly on the {_ordinal(tpl.recurrence_day or 1)}"
            else:
                recurrence_label = "Daily"
            item = QListWidgetItem(
                f"{tpl.title}\n{recurrence_label} · {state}"
            )
            item.setData(Qt.UserRole, tpl.id)
            item.setToolTip(f"{tpl.title} — {state.lower()}")
            self._list.addItem(item)
            if selected_id == tpl.id:
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count():
            self._list.setCurrentRow(0)
        self._selection_changed(self._list.currentItem())

    def _selected_template(self):
        template_id = self._selected_id()
        if template_id is None:
            return None
        return next(
            (template for template in self.service.get_goal_templates() if template.id == template_id),
            None,
        )

    def _selection_changed(self, current, _previous=None) -> None:
        template = self._selected_template() if current is not None else None
        enabled = template is not None
        self._edit_btn.setEnabled(enabled)
        self._toggle_btn.setEnabled(enabled)
        self._delete_btn.setEnabled(enabled)
        if template is None:
            self._selection_label.setText(
                "Select a template to edit, disable, or delete it."
            )
            self._toggle_btn.setText("Enable / Disable")
            return
        state = "active" if template.is_active else "disabled"
        self._selection_label.setText(f"Selected: {template.title} ({state})")
        self._toggle_btn.setText("Disable" if template.is_active else "Enable")

    def _selected_id(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _new(self) -> None:
        dlg = TemplateDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            template = self.service.add_goal_template(
                d["title"], d["notes"], d["recurrence"], d["milestones"],
                d["recurrence_day"],
            )
            self._reload(template.id if template else None)

    def _edit(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        tpl = self._selected_template()
        if not tpl:
            return
        dlg = TemplateDialog(self, template=tpl)
        if dlg.exec():
            d = dlg.get_data()
            self.service.update_goal_template(
                tid, d["title"], d["notes"], d["recurrence"], d["milestones"],
                d["recurrence_day"],
            )
            self._reload(tid)

    def _toggle_active(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        tpl = self._selected_template()
        if tpl:
            self.service.set_goal_template_active(tid, not tpl.is_active)
            self._reload(tid)

    def _delete(self) -> None:
        tid = self._selected_id()
        if tid is None:
            return
        template = self._selected_template()
        if template is None:
            return
        if QMessageBox.question(
            self, "Delete Template",
            f'Delete “{template.title}”? Already-generated goals are kept.',
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.service.delete_goal_template(tid)
            self._reload()
