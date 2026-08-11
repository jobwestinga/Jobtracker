"""Polished Goal create/edit and focused milestone-checklist dialogs."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QPoint, QTimer, Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
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

from ...core.themes import DEFAULT_TOKENS
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    dialog_owner,
    information,
    open_dialog,
    question,
    warning,
)
from .reorderable_list import ReorderableCardList
from .todo_task_item import _StarBurstOverlay


def _tokens_from(parent, explicit: dict | None = None) -> dict:
    if explicit:
        return explicit
    current = parent
    while current is not None:
        tokens = getattr(current, "_tokens", None)
        if tokens:
            return tokens
        current = current.parent() if hasattr(current, "parent") else None
    return DEFAULT_TOKENS


def apply_goal_edits(service, goal_id: int, data: dict) -> None:
    """Apply one edit-dialog result, including milestone add/edit/delete."""
    service.update_todo_task(goal_id, data["name"], data["notes"], data.get("deadline"))
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
        note = entry.get("note")
        if note is None:
            note = existing[milestone_id].note if milestone_id in existing else ""
        if milestone_id is None:
            service.add_milestone(goal_id, entry["title"], note)
        else:
            service.update_milestone(
                milestone_id,
                entry["title"],
                note,
            )


class GoalDialog(InlineDialog):
    """Create/edit the Goal and its complete milestone definition."""

    DELETE_RESULT = 2

    def __init__(
        self,
        parent=None,
        goal=None,
        milestones=None,
        tokens: dict | None = None,
    ) -> None:
        super().__init__(parent)
        configure_window_modal(self)
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

        layout.addWidget(self._label("Description"))
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Why this matters and what success looks like …")
        self.desc_input.setMaximumHeight(100)
        if self.goal and self.goal.notes:
            self.desc_input.setPlainText(self.goal.notes)
        layout.addWidget(self.desc_input)

        # Optional target date (no quota — just a soft "aim for" date).
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        self.target_check = QCheckBox("Target date")
        self.target_check.setCursor(Qt.PointingHandCursor)
        date_row.addWidget(self.target_check)
        self.target_date = QDateEdit()
        self.target_date.setCalendarPopup(True)
        self.target_date.setDisplayFormat("yyyy-MM-dd")
        self.target_date.setDate(QDate.currentDate().addMonths(1))
        self.target_date.setEnabled(False)
        self.target_check.toggled.connect(self.target_date.setEnabled)
        date_row.addWidget(self.target_date)
        date_row.addStretch()
        layout.addLayout(date_row)
        if self.goal and self.goal.deadline:
            try:
                d = date.fromisoformat(self.goal.deadline[:10])
                self.target_date.setDate(QDate(d.year, d.month, d.day))
                self.target_check.setChecked(True)
            except ValueError:
                pass

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
                self._add_milestone_row(
                    milestone.title,
                    milestone.id,
                    milestone.note,
                )
        else:
            self._add_milestone_row()

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        if self._is_edit:
            self.delete_btn = QPushButton("Delete Goal")
            self.delete_btn.setObjectName("dangerBtn")
            self.delete_btn.setMinimumHeight(38)
            self.delete_btn.setCursor(Qt.PointingHandCursor)
            self.delete_btn.clicked.connect(self._confirm_delete)
            btn_row.addWidget(self.delete_btn)
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

    def _add_milestone_row(
        self,
        text: str = "",
        milestone_id: int | None = None,
        note: str = "",
    ) -> None:
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
        remove_btn.setProperty("no_drag", True)
        title_row.addWidget(remove_btn)
        row_layout.addLayout(title_row)

        note_input = QTextEdit()
        note_input.setPlaceholderText("Milestone description (optional) …")
        note_input.setMaximumHeight(58)
        note_input.setPlainText(note or "")
        row_layout.addWidget(note_input)

        entry = {
            "id": milestone_id,
            "input": title_input,
            "note_input": note_input,
            "frame": frame,
        }
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
            warning(self, "Validation", "Goal title cannot be empty.")
            return
        self.accept()

    def _confirm_delete(self) -> None:
        question(
            self,
            "Delete Goal",
            f'Delete “{self.goal.name}” and all of its milestones permanently?',
            self._finish_confirm_delete,
        )

    def _finish_confirm_delete(
        self, answer: QMessageBox.StandardButton
    ) -> None:
        if answer == QMessageBox.Yes:
            self.done(self.DELETE_RESULT)

    def get_data(self) -> dict:
        milestones = []
        for entry in self._milestone_rows:
            title = entry["input"].text().strip()
            if title:
                milestones.append(
                    {
                        "id": entry["id"],
                        "title": title,
                        "note": entry["note_input"].toPlainText().strip(),
                    }
                )
        deadline = None
        if self.target_check.isChecked():
            q = self.target_date.date()
            deadline = date(q.year(), q.month(), q.day()).isoformat()
        return {
            "name": self.title_input.text().strip(),
            "notes": self.desc_input.toPlainText().strip(),
            "deadline": deadline,
            "milestones": milestones,
        }


class GoalDetailDialog(InlineDialog):
    """Focused, read-only definition with a polished milestone checklist."""

    def __init__(
        self,
        service,
        goal_id: int,
        parent=None,
        tokens: dict | None = None,
    ) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.service = service
        self.goal_id = goal_id
        self._tokens = _tokens_from(parent, tokens)
        self._milestone_checks: list[QCheckBox] = []
        # No QShortcuts here on purpose: inline editors share the main QWindow,
        # so registering 1–9 / Ctrl+Z again makes Qt report ambiguous window
        # shortcuts before the key reaches this editor. InlineDialog's
        # application event filter routes those keys to handle_inline_key.
        self.setWindowTitle("Goal")
        self.setMinimumSize(520, 600)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(12)

        self.desc_lbl = QLabel("")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(
            f"font-size: 13px; color: {self._tokens['TEXT_SECONDARY']};"
            " border: none; background: transparent; padding: 2px 1px 8px 1px;"
        )
        layout.addWidget(self.desc_lbl)

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

        self._ms_list = ReorderableCardList(spacing=8)
        self._ms_list.order_changed.connect(self._milestone_order_changed)
        layout.addWidget(self._ms_list, 1)

        self._ms_empty = QLabel("No milestones yet. Use Edit Goal to add them.")
        self._ms_empty.setAlignment(Qt.AlignCenter)
        self._ms_empty.setStyleSheet(
            f"color: {self._tokens['TEXT_DIMMED']}; font-style: italic; padding: 28px;"
        )
        self._ms_empty.hide()
        layout.addWidget(self._ms_empty)

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
        self.setWindowTitle(goal.name)
        self.desc_lbl.setText(goal.notes or "No description yet.")

        view_state = self._ms_list.capture_view_state()
        self._ms_list.clear_cards()
        self._milestone_checks.clear()

        milestones = self.service.get_goal_milestones(self.goal_id)
        if milestones:
            self._ms_empty.hide()
            self._ms_list.show()
            for index, milestone in enumerate(milestones, start=1):
                row = self._milestone_row(
                    milestone,
                    shortcut_number=index,
                )
                self._ms_list.add_card(milestone.id, row)
                self._install_milestone_shortcut(row)
            self._ms_list.restore_view_state(view_state)
        else:
            self._ms_list.hide()
            self._ms_empty.show()

        done, total = self.service.get_goal_progress(self.goal_id)
        self.progress_lbl.setText(f"{done} / {total} complete" if total else "No milestones")

        if goal.is_completed:
            self.complete_btn.setText("Reopen Goal")
            self.complete_btn.setEnabled(True)
        else:
            self.complete_btn.setText("Mark Complete")
            self.complete_btn.setEnabled(self.service.can_complete_goal(self.goal_id))

    def _milestone_row(
        self, milestone, shortcut_number: int | None = None
    ) -> QFrame:
        frame = QFrame()
        frame.setMinimumHeight(52)
        frame.setStyleSheet(
            f"QFrame {{ background: {self._tokens.get('CARD_BG', self._tokens['BG_SECONDARY'])};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']}; border-radius: 11px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(13, 9, 13, 9)
        layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        frame._jt_title_row = title_row
        frame._jt_shortcut_number = shortcut_number

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
            lambda checked, milestone_id=milestone.id, source=check: self._toggle_milestone(
                milestone_id, checked, source
            )
        )
        self._milestone_checks.append(check)
        title_row.addWidget(check, 1)
        layout.addLayout(title_row)

        if milestone.note:
            note = QLabel(milestone.note)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"font-size: 11px; color: {self._tokens['TEXT_SECONDARY']};"
                " padding-left: 30px; border: none; background: transparent;"
            )
            layout.addWidget(note)
        return frame

    def _install_milestone_shortcut(self, frame: QFrame) -> None:
        """Create a milestone keycap after its row is attached to the list."""
        number = frame._jt_shortcut_number
        if number is None:
            return
        shortcut = QLabel(str(number), frame)
        shortcut.setProperty("_jt_shortcut_badge", True)
        shortcut.setProperty(
            "_jt_shortcut_tooltip",
            "Press {number} to toggle this milestone",
        )
        shortcut.setAlignment(Qt.AlignCenter)
        shortcut.setFixedSize(22, 22)
        shortcut.setToolTip(f"Press {number} to toggle this milestone")
        shortcut.setStyleSheet(
            f"background: {self._tokens.get('BG_TERTIARY', self._tokens['BG_SECONDARY'])};"
            f" border: 1px solid {self._tokens['BORDER_COLOR']};"
            f" border-radius: 6px; color: {self._tokens['TEXT_SECONDARY']};"
            " font-size: 10px; font-weight: 750;"
        )
        frame._jt_title_row.insertWidget(0, shortcut, 0, Qt.AlignTop)
        shortcut.setVisible(1 <= number <= 9)

    def _milestone_order_changed(self, ordered_ids: list[int]) -> None:
        previous = [
            milestone.id
            for milestone in self.service.get_goal_milestones(self.goal_id)
            if milestone.id is not None
        ]
        if previous == ordered_ids:
            return
        self.service.set_milestone_order(self.goal_id, ordered_ids)
        owner = dialog_owner(self)
        if hasattr(owner, "_register_undo"):
            owner._register_undo(
                lambda old=previous, service=self.service, goal_id=self.goal_id: (
                    service.set_milestone_order(
                        goal_id, old
                    )
                )
            )

    def _perform_undo(self) -> None:
        owner = dialog_owner(self)
        if hasattr(owner, "_perform_undo"):
            owner._perform_undo(force=True)
            self._reload()

    def handle_inline_key(self, event) -> bool:
        if event.matches(QKeySequence.Undo):
            self._perform_undo()
            return True
        if event.modifiers() & (
            Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        ):
            return False
        text = event.text()
        if len(text) == 1 and text in "123456789":
            self._toggle_milestone_by_number(int(text) - 1)
            return True
        return False

    def claims_inline_key(self, event) -> bool:
        return bool(
            event.matches(QKeySequence.Undo)
            or (
                not event.modifiers()
                & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
                and len(event.text()) == 1
                and event.text() in "123456789"
            )
        )

    def done(self, result: int) -> None:
        if hasattr(self, "_milestone_reload_timer"):
            self._milestone_reload_timer.stop()
        super().done(result)

    def _toggle_milestone_by_number(self, index: int) -> None:
        if 0 <= index < len(self._milestone_checks):
            checkbox = self._milestone_checks[index]
            if checkbox.isEnabled():
                checkbox.click()

    def _star_colors(self) -> list:
        t = self._tokens
        return [
            t.get("ACCENT_GREEN", "#22C55E"),
            t.get("ACCENT", "#3B82F6"),
            t.get("TEXT_PRIMARY", "#FFFFFF"),
            t.get("ACCENT_GREEN", "#22C55E"),
        ]

    def _toggle_milestone(
        self,
        milestone_id: int,
        checked: bool,
        source: QCheckBox,
    ) -> None:
        self.service.set_milestone_done(milestone_id, checked)
        owner = dialog_owner(self)
        if hasattr(owner, "_register_undo"):
            owner._register_undo(
                lambda mid=milestone_id, old=not checked, service=self.service: (
                    service.set_milestone_done(mid, old)
                )
            )
        if checked:
            # QCheckBox spans the full milestone row; its visual indicator is
            # the 18px circle at the left, not the widget's geometric center.
            indicator_center = QPoint(9, source.height() // 2)
            origin = source.mapTo(self, indicator_center)
            burst = _StarBurstOverlay(self, origin, count=12, colors=self._star_colors())
            burst.start()
            if hasattr(self, "_milestone_reload_timer"):
                self._milestone_reload_timer.stop()
            self._milestone_reload_timer = QTimer(self)
            self._milestone_reload_timer.setSingleShot(True)
            self._milestone_reload_timer.timeout.connect(self._reload)
            self._milestone_reload_timer.start(320)
        else:
            self._reload()

    def _edit_goal(self) -> None:
        milestones = self.service.get_goal_milestones(self.goal_id)
        dialog = GoalDialog(
            self,
            goal=self._goal,
            milestones=milestones,
            tokens=self._tokens,
        )
        open_dialog(dialog, self._finish_edit_goal)

    def _finish_edit_goal(self, result: int, dialog: QDialog) -> None:
        if result == GoalDialog.DELETE_RESULT:
            self.service.delete_todo_task(self.goal_id)
            self.accept()
        elif result == QDialog.Accepted:
            apply_goal_edits(self.service, self.goal_id, dialog.get_data())
            self._reload()

    def _toggle_complete(self) -> None:
        goal = self.service.get_goal(self.goal_id)
        if goal and goal.is_completed:
            self.service.uncomplete_goal(self.goal_id)
            self._reload()
            return
        if not self.service.complete_goal(self.goal_id):
            information(
                self,
                "Milestones remain",
                "Finish all milestones before completing this goal.",
            )
            return
        self.accept()
