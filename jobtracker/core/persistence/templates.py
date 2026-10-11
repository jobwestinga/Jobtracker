"""Recurring template persistence and schedule normalization."""

from __future__ import annotations

from typing import List, Optional

from ..models import GoalTemplate


class TemplateRepository:
    """Recurring template persistence and schedule normalization."""

    def __init__(self, database):
        self._database = database

    def add_goal_template(
        self, title: str, notes: str, recurrence: str, milestones_json: str = "[]",
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        if recurrence not in ("daily", "weekly", "monthly"):
            recurrence = "daily"
        recurrence_day = self._database._normalize_recurrence_day(recurrence, recurrence_day)
        cur = self._database.connection.cursor()
        cur.execute(
            "INSERT INTO goal_templates "
            "(title, notes, recurrence, recurrence_day, milestones_json, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (normalized, (notes or "").strip(), recurrence, recurrence_day,
             milestones_json or "[]",
             self._database._next_sort_order("goal_templates")),
        )
        self._database.connection.commit()
        return self._database.get_goal_template(cur.lastrowid)

    def get_goal_template(self, template_id: int) -> Optional[GoalTemplate]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM goal_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        return GoalTemplate(**dict(row)) if row else None

    def get_goal_templates(self, active_only: bool = False) -> List[GoalTemplate]:
        cur = self._database.connection.cursor()
        if active_only:
            cur.execute("SELECT * FROM goal_templates WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
        else:
            cur.execute("SELECT * FROM goal_templates ORDER BY sort_order ASC, id ASC")
        return [GoalTemplate(**dict(row)) for row in cur.fetchall()]

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str,
        milestones_json: str, recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        if recurrence not in ("daily", "weekly", "monthly"):
            recurrence = "daily"
        recurrence_day = self._database._normalize_recurrence_day(recurrence, recurrence_day)
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET title = ?, notes = ?, recurrence = ?, "
            "recurrence_day = ?, milestones_json = ?, "
            "last_generated = CASE "
            "WHEN recurrence <> ? OR COALESCE(recurrence_day, 0) <> COALESCE(?, 0) "
            "THEN NULL ELSE last_generated END WHERE id = ?",
            (
                normalized, (notes or "").strip(), recurrence, recurrence_day,
                milestones_json or "[]", recurrence, recurrence_day, template_id,
            ),
        )
        self._database.connection.commit()
        return self._database.get_goal_template(template_id)

    @staticmethod
    def _normalize_recurrence_day(
        recurrence: str, recurrence_day: Optional[int]
    ) -> Optional[int]:
        if recurrence == "daily":
            return None
        try:
            value = int(recurrence_day) if recurrence_day is not None else 1
        except (TypeError, ValueError):
            value = 1
        return max(1, min(7 if recurrence == "weekly" else 31, value))

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET is_active = ? WHERE id = ?",
            (1 if active else 0, template_id),
        )
        self._database.connection.commit()

    def delete_goal_template(self, template_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("DELETE FROM goal_templates WHERE id = ?", (template_id,))
        self._database.connection.commit()

    def set_goal_template_last_generated(self, template_id: int, period_key: str) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET last_generated = ? WHERE id = ?",
            (period_key, template_id),
        )
        self._database.connection.commit()
