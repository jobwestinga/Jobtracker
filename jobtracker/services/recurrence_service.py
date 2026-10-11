"""Additive period-idempotent recurring goal generation."""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime
from typing import List, Optional

from ..core.models import GoalTemplate
from ..core import timeutils

logger = logging.getLogger("jobtracker")


class RecurrenceService:
    """Templates and generation; no independent mutable domain state."""

    def __init__(self, tracker):
        self._tracker = tracker

    @staticmethod
    def build_milestones_json(titles: list) -> str:
        """Serialize milestone specs (strings or {title,note}) for a template."""
        items = []
        for entry in titles:
            if isinstance(entry, dict):
                title = (entry.get("title") or "").strip()
                note = (entry.get("note") or "").strip()
            else:
                title = str(entry).strip()
                note = ""
            if title:
                items.append({"title": title, "note": note})
        return json.dumps(items)

    def get_goal_templates(self, active_only: bool = False) -> List[GoalTemplate]:
        return self._tracker.db.get_goal_templates(active_only=active_only)

    def add_goal_template(
        self, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        ms_json = self._tracker.build_milestones_json(milestone_titles or [])
        return self._tracker.db.add_goal_template(
            title, notes, recurrence, ms_json, recurrence_day
        )

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        ms_json = self._tracker.build_milestones_json(milestone_titles or [])
        return self._tracker.db.update_goal_template(
            template_id, title, notes, recurrence, ms_json, recurrence_day
        )

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        self._tracker.db.set_goal_template_active(template_id, active)

    def delete_goal_template(self, template_id: int) -> None:
        self._tracker.db.delete_goal_template(template_id)

    @staticmethod
    def _template_period_key(recurrence: str, logical: date) -> str:
        """The logical-period key a template generates against. Distinct keys ->
        a new generation is due; identical key -> already generated."""
        if recurrence == "weekly":
            return timeutils.week_start(logical).isoformat()
        if recurrence == "monthly":
            return timeutils.month_start(logical).isoformat()
        return logical.isoformat()

    @staticmethod
    def _template_instance_title(title: str, logical: date) -> str:
        """Resolve supported placeholders in a generated goal title."""
        return title.replace("{date}", logical.isoformat())

    @staticmethod
    def _template_is_due(template: GoalTemplate, logical: date) -> bool:
        if template.recurrence == "weekly":
            return logical.isoweekday() >= (template.recurrence_day or 1)
        if template.recurrence == "monthly":
            scheduled = min(
                template.recurrence_day or 1,
                calendar.monthrange(logical.year, logical.month)[1],
            )
            return logical.day >= scheduled
        return True

    def generate_due_goal_instances(self, now: Optional[datetime] = None) -> list[int]:
        """Append a Goal instance to the top of the list for any active template
        whose current logical period hasn't been generated yet. Idempotent within
        a period (safe to call on every launch). Returns the new goal ids.

        Uncompleted goals from previous periods are never touched — they simply
        remain in the list. Generation is purely additive: nothing is ever
        auto-removed, so a skipped day stays visible as a backlog item.
        """
        now = now or datetime.now()
        day_start = self._tracker.get_day_start()
        logical = timeutils.logical_day(now, day_start)

        created: list[int] = []
        for tpl in self._tracker.db.get_goal_templates(active_only=True):
            if not self._tracker._template_is_due(tpl, logical):
                continue
            key = self._tracker._template_period_key(tpl.recurrence, logical)
            if tpl.last_generated == key:
                continue  # already generated for this period -> no duplicate

            instance_title = self._tracker._template_instance_title(tpl.title, logical)
            goal = self._tracker.db.add_todo_task_at_top(
                instance_title,
                tpl.notes,
                template_id=tpl.id,
            )
            try:
                milestones = json.loads(tpl.milestones_json or "[]")
            except (ValueError, TypeError):
                milestones = []
            for entry in milestones:
                if isinstance(entry, dict):
                    title = (entry.get("title") or "").strip()
                    note = (entry.get("note") or "").strip()
                else:
                    title, note = str(entry).strip(), ""
                if title:
                    self._tracker.db.add_milestone(goal.id, title, note)

            self._tracker.db.set_goal_template_last_generated(tpl.id, key)
            if goal.id is not None:
                created.append(goal.id)
            logger.info(
                "Generated goal '%s' from template %s for %s",
                instance_title,
                tpl.id,
                key,
            )

        return created
