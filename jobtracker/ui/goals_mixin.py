"""
Goals page: building, reload, recurring generation, and all goal actions.

Mixed into MainWindow. Relies on shared attributes/methods of the host window:
``self.service``, ``self._tokens``, ``self._pages``, ``self._open_settings``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .widgets.goal_dialog import GoalDetailDialog, GoalDialog, apply_goal_edits
from .widgets.reorderable_list import ReorderableCardList
from .widgets.template_dialog import TemplateManagerDialog
from .widgets.todo_task_item import TodoTaskItemWidget

logger = logging.getLogger("jobtracker")


class GoalsMixin:
    def _build_tasks_page(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        header = QHBoxLayout()
        self._goals_title = QLabel("Goals")
        self._goals_title.setObjectName("title")
        header.addWidget(self._goals_title)
        header.addStretch()

        add_btn = QPushButton("+ Goal")
        add_btn.setObjectName("primaryBtn")
        add_btn.setMinimumHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._new_goal)
        header.addWidget(add_btn)

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setMinimumHeight(34)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.clicked.connect(self._open_settings)
        header.addWidget(gear_btn)

        lay.addLayout(header)

        tools = QHBoxLayout()
        templates_btn = QPushButton("Templates")
        templates_btn.setCursor(Qt.PointingHandCursor)
        templates_btn.setMinimumHeight(30)
        templates_btn.setToolTip("Recurring goal templates (daily/weekly/monthly)")
        templates_btn.clicked.connect(self._open_templates)
        tools.addWidget(templates_btn)
        tools.addStretch()
        self._show_completed_btn = QPushButton("Completed")
        self._show_completed_btn.setCursor(Qt.PointingHandCursor)
        self._show_completed_btn.setMinimumHeight(30)
        self._show_completed_btn.clicked.connect(self._toggle_completed_goals)
        tools.addWidget(self._show_completed_btn)
        lay.addLayout(tools)

        self._todo_empty = QLabel("No goals yet — click + Goal to add one.")
        self._todo_empty.setStyleSheet(
            f"color: {self._tokens['TEXT_DIMMED']}; font-style: italic; padding: 20px 0;"
        )
        self._todo_empty.setAlignment(Qt.AlignCenter)
        self._todo_empty.hide()
        lay.addWidget(self._todo_empty)

        self._todo_list = ReorderableCardList(spacing=8)
        self._todo_list.order_changed.connect(self._on_todo_order_changed)
        lay.addWidget(self._todo_list, 1)
        self._goal_view_states = {False: None, True: None}
        self._goals_rendered_view = False

        self._pages.addWidget(page)

    def _generate_due_goals(self, reload: bool = True) -> None:
        """Generate recurring goal instances due for the current logical period."""
        try:
            created = self.service.generate_due_goal_instances()
        except Exception:
            logger.exception("Recurring goal generation failed")
            return
        if created and reload and hasattr(self, "_todo_list"):
            self._reload_tasks()

    def _reload_tasks(self) -> None:
        rendered_view = getattr(
            self, "_goals_rendered_view", self._showing_completed_goals
        )
        self._goal_view_states[rendered_view] = (
            self._todo_list.capture_view_state()
        )
        restore_state = self._goal_view_states.get(
            self._showing_completed_goals
        )
        self._todo_list.clear_cards()
        self._goals_rendered_view = self._showing_completed_goals

        goals = (
            self.service.get_completed_goals()
            if self._showing_completed_goals
            else self.service.get_active_goals()
        )
        self._show_completed_btn.setText(
            "Back to Active" if self._showing_completed_goals else "Completed"
        )
        self._goals_title.setText(
            "Completed Goals" if self._showing_completed_goals else "Goals"
        )
        self._todo_list.setDragEnabled(True)
        self._todo_list.setAcceptDrops(True)

        if not goals:
            self._todo_empty.setText(
                "No completed goals yet."
                if self._showing_completed_goals
                else "No goals yet — click + Goal to add one."
            )
            self._todo_empty.show()
            self._todo_list.hide()
            return
        self._todo_empty.hide()
        self._todo_list.show()

        for index, goal in enumerate(goals, start=1):
            if goal.id is None:
                continue
            progress = self.service.get_goal_progress(goal.id)
            card = TodoTaskItemWidget(
                goal,
                self._tokens,
                progress=progress,
                shortcut_number=index,
            )
            card.open_requested.connect(self._open_goal)
            card.edit_requested.connect(self._edit_goal)
            card.delete_requested.connect(self._delete_goal)
            card.complete_requested.connect(self._complete_goal)
            self._todo_list.add_card(goal.id, card)
        self._todo_list.restore_view_state(restore_state)

    # ── Goal actions ────────────────────────────────────────────────────
    def _new_goal(self) -> None:
        dlg = GoalDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            goal = self.service.add_todo_task(d["name"], d["notes"], d.get("deadline"))
            if goal is None or goal.id is None:
                QMessageBox.warning(self, "Validation", "Goal title cannot be empty.")
                return
            for milestone in d["milestones"]:
                self.service.add_milestone(
                    goal.id,
                    milestone["title"],
                    milestone.get("note", ""),
                )
            self._showing_completed_goals = False
            self._reload_tasks()

    def _open_goal(self, goal_id: int) -> None:
        if self.service.get_goal(goal_id) is None:
            return
        GoalDetailDialog(self.service, goal_id, self, tokens=self._tokens).exec()
        self._reload_tasks()

    def _edit_goal(self, goal_id: int) -> None:
        goal = self.service.get_goal(goal_id)
        if goal is None:
            return
        dlg = GoalDialog(
            self,
            goal=goal,
            milestones=self.service.get_goal_milestones(goal_id),
            tokens=self._tokens,
        )
        result = dlg.exec()
        if result == GoalDialog.DELETE_RESULT:
            self.service.delete_todo_task(goal_id)
            self._reload_tasks()
        elif result == QDialog.Accepted:
            apply_goal_edits(self.service, goal_id, dlg.get_data())
            self._reload_tasks()

    def _delete_goal(self, goal_id: int) -> None:
        goal = self.service.get_goal(goal_id)
        if goal is None:
            return
        if QMessageBox.question(
            self,
            "Delete Goal",
            f'Delete “{goal.name}” and all of its milestones permanently?',
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.service.delete_todo_task(goal_id)
            self._reload_tasks()

    def _complete_goal(self, goal_id: int) -> None:
        # The card only emits this when completion is allowed (it shakes the ✓
        # otherwise), so we just guard quietly and run the fade-out.
        if not self.service.can_complete_goal(goal_id):
            return

        def after_anim() -> None:
            self.service.complete_goal(goal_id)
            self._reload_tasks()

        self._todo_list.animate_remove(goal_id, on_finished=after_anim)

    def _toggle_completed_goals(self) -> None:
        self._showing_completed_goals = not self._showing_completed_goals
        self._reload_tasks()

    def _open_templates(self) -> None:
        TemplateManagerDialog(self.service, self).exec()
        self._generate_due_goals(reload=False)
        self._reload_tasks()

    def _on_todo_order_changed(self, ordered_ids: list[int]) -> None:
        completed = self._showing_completed_goals
        goals = (
            self.service.get_completed_goals()
            if completed
            else self.service.get_active_goals()
        )
        previous = [goal.id for goal in goals if goal.id is not None]
        if previous == ordered_ids:
            return
        self.service.set_todo_task_order(
            ordered_ids, completed=completed
        )
        if hasattr(self, "_register_undo"):
            self._register_undo(
                lambda old=previous, completed_view=completed: (
                    self.service.set_todo_task_order(
                        old, completed=completed_view
                    )
                )
            )
