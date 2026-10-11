"""Goals, milestones and the shared completion invariant."""

from __future__ import annotations

from typing import List, Optional

from ..core.models import TodoTask, Milestone


class GoalService:
    """Goal rules share the facade database and preserve subclass dispatch."""

    def __init__(self, tracker):
        self._tracker = tracker

    def get_all_todo_tasks(self) -> List[TodoTask]:
        return self._tracker.db.get_all_todo_tasks()

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return self._tracker.db.add_todo_task(normalized_name, normalized_notes, normalized_deadline)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return self._tracker.db.update_todo_task(todo_task_id, normalized_name, normalized_notes, normalized_deadline)

    def delete_todo_task(self, todo_task_id: int) -> None:
        self._tracker.db.delete_todo_task(todo_task_id)

    def set_todo_task_order(
        self, ordered_ids: list[int], completed: bool = False
    ) -> None:
        self._tracker.db.set_todo_task_order(ordered_ids, completed=completed)

    def get_active_goals(self) -> List[TodoTask]:
        self._tracker.db.ensure_manual_goal_order()
        return self._tracker.db.get_all_todo_tasks()

    def get_completed_goals(self) -> List[TodoTask]:
        return self._tracker.db.get_completed_todo_tasks()

    def get_goal(self, goal_id: int) -> Optional[TodoTask]:
        return self._tracker.db.get_todo_task(goal_id)

    def get_goal_milestones(self, goal_id: int) -> List[Milestone]:
        return self._tracker.db.get_milestones(goal_id)

    def get_goal_progress(self, goal_id: int) -> tuple[int, int]:
        """(milestones_done, milestones_total) for a goal."""
        milestones = self._tracker.db.get_milestones(goal_id)
        done = sum(1 for m in milestones if m.is_done)
        return done, len(milestones)

    def can_complete_goal(self, goal_id: int) -> bool:
        """A goal may be completed only when all milestones are done, or when it
        has no milestones at all (then manual completion is always allowed)."""
        if self._tracker.db.get_todo_task(goal_id) is None:
            return False
        done, total = self._tracker.get_goal_progress(goal_id)
        return total == 0 or done == total

    def complete_goal(self, goal_id: int) -> bool:
        """Mark a goal complete. Refuses if milestones remain unchecked."""
        if not self._tracker.can_complete_goal(goal_id):
            return False
        self._tracker.db.complete_todo_task(goal_id)
        return True

    def uncomplete_goal(self, goal_id: int) -> None:
        self._tracker.db.uncomplete_todo_task(goal_id)

    def toggle_goal_focused(self, goal_id: int) -> bool:
        """Flip the weekly-focus flag. Returns the new state (False if the
        goal does not exist)."""
        goal = self._tracker.db.get_todo_task(goal_id)
        if goal is None:
            return False
        focused = not bool(goal.is_focused)
        self._tracker.db.set_todo_task_focused(goal_id, focused)
        return focused

    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        milestone = self._tracker.db.add_milestone(goal_id, title, note)
        if milestone is not None:
            goal = self._tracker.db.get_todo_task(goal_id)
            if goal and goal.is_completed:
                self._tracker.db.uncomplete_todo_task(goal_id)
        return milestone

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self._tracker.db.update_milestone(milestone_id, title, note)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        milestone = self._tracker.db.get_milestone(milestone_id)
        self._tracker.db.set_milestone_done(milestone_id, done)
        # A completed goal may never contain an unchecked milestone. Reopen it
        # automatically so completion remains an honest, reversible invariant.
        if milestone is not None and not done:
            goal = self._tracker.db.get_todo_task(milestone.goal_id)
            if goal and goal.is_completed:
                self._tracker.db.uncomplete_todo_task(milestone.goal_id)

    def delete_milestone(self, milestone_id: int) -> None:
        self._tracker.db.delete_milestone(milestone_id)

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        self._tracker.db.set_milestone_order(goal_id, ordered_ids)
