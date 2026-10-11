"""Goal and milestone SQL with legacy todo_tasks compatibility."""

from __future__ import annotations

from typing import List, Optional

from ..models import TodoTask, Milestone


class GoalRepository:
    """Goal and milestone SQL with legacy todo_tasks compatibility."""

    def __init__(self, database):
        self._database = database

    def get_incomplete_todo_count(self) -> int:
        cur = self._database.connection.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM todo_tasks WHERE is_completed = 0")
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0

    def _todo_order_mode(self) -> str:
        mode = self._database.get_setting("todo_order_mode", "deadline")
        if mode in {"deadline", "manual"}:
            return mode
        return "deadline"

    def _todo_ids_deadline_order(self) -> list[int]:
        cur = self._database.connection.cursor()
        cur.execute(
            """
            SELECT id FROM todo_tasks
            ORDER BY
                CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END ASC,
                deadline ASC,
                sort_order ASC,
                created_at ASC
            """
        )
        return [int(row["id"]) for row in cur.fetchall()]

    def _ensure_manual_todo_order(self) -> None:
        if self._database._todo_order_mode() == "manual":
            return
        ordered_ids = self._database._todo_ids_deadline_order()
        self._database._set_order("todo_tasks", "id", ordered_ids)
        self._database.set_setting("todo_order_mode", "manual")

    def ensure_manual_goal_order(self) -> None:
        """Migrate the legacy deadline-sorted Tasks view to manual Goal order.

        The currently visible deadline order is materialised first, so the
        redesign does not unexpectedly scramble existing items.
        """
        self._database._ensure_manual_todo_order()

    def sort_todo_tasks_by_deadline(self) -> None:
        ordered_ids = self._database._todo_ids_deadline_order()
        self._database._set_order("todo_tasks", "id", ordered_ids)
        self._database.set_setting("todo_order_mode", "deadline")

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> TodoTask:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Todo task name cannot be empty")

        cur = self._database.connection.cursor()
        cur.execute(
            "INSERT INTO todo_tasks (name, notes, deadline, sort_order) VALUES (?, ?, ?, ?)",
            (normalized_name, (notes or "").strip(), deadline or None, self._database._next_sort_order("todo_tasks")),
        )
        self._database.connection.commit()
        todo_task_id = cur.lastrowid
        if self._database._todo_order_mode() == "deadline":
            self._database.sort_todo_tasks_by_deadline()
        return self._database.get_todo_task(todo_task_id)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET name = ?, notes = ?, deadline = ? WHERE id = ?",
            (normalized_name, (notes or "").strip(), deadline or None, todo_task_id),
        )
        self._database.connection.commit()
        if self._database._todo_order_mode() == "deadline":
            self._database.sort_todo_tasks_by_deadline()
        return self._database.get_todo_task(todo_task_id)

    def delete_todo_task(self, todo_task_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("DELETE FROM todo_tasks WHERE id = ?", (todo_task_id,))
        self._database.connection.commit()

    def get_todo_task(self, todo_task_id: int) -> Optional[TodoTask]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM todo_tasks WHERE id = ?", (todo_task_id,))
        row = cur.fetchone()
        return TodoTask(**dict(row)) if row else None

    def get_all_todo_tasks(self) -> List[TodoTask]:
        cur = self._database.connection.cursor()
        if self._database._todo_order_mode() == "manual":
            cur.execute(
                "SELECT * FROM todo_tasks WHERE is_completed = 0 "
                "ORDER BY sort_order ASC, created_at ASC"
            )
        else:
            cur.execute(
                """
                SELECT * FROM todo_tasks
                WHERE is_completed = 0
                ORDER BY
                    CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END ASC,
                    deadline ASC,
                    sort_order ASC,
                    created_at ASC
                """
            )
        return [TodoTask(**dict(row)) for row in cur.fetchall()]

    def get_completed_todo_tasks(self) -> List[TodoTask]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT * FROM todo_tasks WHERE is_completed = 1 "
            "ORDER BY sort_order ASC, created_at ASC, id ASC"
        )
        return [TodoTask(**dict(row)) for row in cur.fetchall()]

    def add_todo_task_at_top(
        self, name: str, notes: str, template_id: Optional[int] = None
    ) -> TodoTask:
        """Insert a Goal at the TOP of the manual order (used by template
        generation). Forces manual ordering so the placement sticks."""
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Goal name cannot be empty")
        cur = self._database.connection.cursor()
        cur.execute("SELECT COALESCE(MIN(sort_order), 1) - 1 AS top FROM todo_tasks")
        top_order = int(cur.fetchone()["top"])
        # Zero is reserved for legacy/uninitialised rows and is normalised during
        # schema setup. Use a negative value for the first generated top item so
        # it remains at the top after the next launch.
        if top_order >= 0:
            top_order = -1
        cur.execute(
            "INSERT INTO todo_tasks (name, notes, deadline, sort_order, template_id) "
            "VALUES (?, ?, NULL, ?, ?)",
            (normalized_name, (notes or "").strip(), top_order, template_id),
        )
        self._database.connection.commit()
        self._database.set_setting("todo_order_mode", "manual")
        return self._database.get_todo_task(cur.lastrowid)

    def complete_todo_task(self, todo_task_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_completed = 1 WHERE id = ?", (todo_task_id,)
        )
        self._database.connection.commit()

    def uncomplete_todo_task(self, todo_task_id: int) -> None:
        """Reopen a completed Goal. Completion is always reversible."""
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_completed = 0 WHERE id = ?", (todo_task_id,)
        )
        self._database.connection.commit()

    def set_todo_task_focused(self, todo_task_id: int, focused: bool) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_focused = ? WHERE id = ?",
            (1 if focused else 0, todo_task_id),
        )
        self._database.connection.commit()

    def set_todo_task_order(
        self, ordered_ids: list[int], completed: bool = False
    ) -> None:
        if not ordered_ids:
            return
        if not completed:
            self._database._ensure_manual_todo_order()
            self._database.set_setting("todo_order_mode", "manual")
        self._database._set_filtered_order(
            "todo_tasks",
            "id",
            ordered_ids,
            "is_completed = ?",
            (1 if completed else 0,),
        )

    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        cur = self._database.connection.cursor()
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS nxt FROM milestones WHERE goal_id = ?", (goal_id,))
        order = int(cur.fetchone()["nxt"])
        cur.execute(
            "INSERT INTO milestones (goal_id, title, note, sort_order) VALUES (?, ?, ?, ?)",
            (goal_id, normalized, (note or "").strip(), order),
        )
        self._database.connection.commit()
        return self._database.get_milestone(cur.lastrowid)

    def get_milestone(self, milestone_id: int) -> Optional[Milestone]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
        row = cur.fetchone()
        return Milestone(**dict(row)) if row else None

    def get_milestones(self, goal_id: int) -> List[Milestone]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT * FROM milestones WHERE goal_id = ? ORDER BY sort_order ASC, id ASC",
            (goal_id,),
        )
        return [Milestone(**dict(row)) for row in cur.fetchall()]

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE milestones SET title = ?, note = ? WHERE id = ?",
            (normalized, (note or "").strip(), milestone_id),
        )
        self._database.connection.commit()
        return self._database.get_milestone(milestone_id)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE milestones SET is_done = ? WHERE id = ?",
            (1 if done else 0, milestone_id),
        )
        self._database.connection.commit()

    def delete_milestone(self, milestone_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
        self._database.connection.commit()

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        for idx, mid in enumerate(ordered_ids, start=1):
            self._database.connection.execute(
                "UPDATE milestones SET sort_order = ? WHERE id = ? AND goal_id = ?",
                (idx, int(mid), goal_id),
            )
        self._database.connection.commit()
