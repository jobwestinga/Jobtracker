"""A TrackerService that also tells the server what it did.

Every mutation is applied **locally first** — so the UI is instant and works with
the server down — and simultaneously queued in the outbox as the equivalent
operation. The server later applies the same operation through the same
``TrackerService`` code, and the row it produces comes back on the next pull and
overwrites the optimistic local copy.

Two rules make the optimism safe:

* **The local row's uid is sent with the operation.** The server adopts it, so
  the row created here and the row created there are the same row. Without this
  the pull would bring back a second copy of everything created offline.
* **Anything that fails locally is not queued.** If ``complete_goal`` refuses
  because a milestone is unchecked, the server would refuse too; queueing it
  would just block the outbox.

**Every write to a synced table must be routed here.** A mutation that reaches
SQLite without a matching operation makes the mirror disagree with the server,
and the integrity check repairs that by re-downloading — which would silently
discard the change. ``tests/test_sync_service.py`` asserts the coverage.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from ..core import sync_policy
from ..services.tracker_service import TrackerService
from . import state

logger = logging.getLogger("jobtracker")


class SyncedTrackerService(TrackerService):
    """TrackerService + an outbox. Drop-in replacement for the UI."""

    def __init__(self, database=None, *, device_id=None, recover=True):
        super().__init__(database, device_id=device_id, recover=recover)
        state.install(self.db.connection)

    # ── queueing ────────────────────────────────────────────────────────
    def _queue(self, op_name: str, params: dict[str, Any]) -> None:
        try:
            state.enqueue(self.db.connection, op_name, params)
        except Exception:  # noqa: BLE001 - a queue failure must not lose the edit
            logger.exception("Could not queue %s; local change kept", op_name)

    def _uid(self, table: str, row_id: Optional[int]) -> Optional[str]:
        return self.db.uid_for_id(table, row_id) if row_id is not None else None

    def _uids(self, table: str, row_ids) -> list[str]:
        out = []
        for row_id in row_ids or []:
            uid = self._uid(table, row_id)
            if uid:
                out.append(uid)
        return out

    @staticmethod
    def _iso(value) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat() if isinstance(value, datetime) else str(value)

    # ── subjects ────────────────────────────────────────────────────────
    def add_subject(self, name, color, notes):
        subject = super().add_subject(name, color, notes)
        if subject is not None:
            self._queue(
                "add_subject",
                {
                    "name": subject.name,
                    "color": subject.color,
                    "notes": subject.notes,
                    "uid": self._uid("tasks", subject.id),
                },
            )
        return subject

    def update_subject(self, subject_id, name, color, notes):
        subject = super().update_subject(subject_id, name, color, notes)
        if subject is not None:
            self._queue(
                "update_subject",
                {
                    "subject_uid": self._uid("tasks", subject_id),
                    "name": subject.name,
                    "color": subject.color,
                    "notes": subject.notes,
                },
            )
        return subject

    def archive_subject(self, subject_id):
        uid = self._uid("tasks", subject_id)
        super().archive_subject(subject_id)
        self._queue("archive_subject", {"subject_uid": uid})

    def unarchive_subject(self, subject_id):
        uid = self._uid("tasks", subject_id)
        super().unarchive_subject(subject_id)
        self._queue("unarchive_subject", {"subject_uid": uid})

    def delete_subject(self, subject_id):
        # Read the uid BEFORE the row goes; afterwards there is nothing to look up.
        uid = self._uid("tasks", subject_id)
        super().delete_subject(subject_id)
        if uid:
            self._queue("delete_subject", {"subject_uid": uid})

    def set_subject_order(self, ordered_ids, archived=False):
        super().set_subject_order(ordered_ids, archived=archived)
        self._queue(
            "set_subject_order",
            {"subject_uids": self._uids("tasks", ordered_ids), "archived": bool(archived)},
        )

    # ── the running timer ───────────────────────────────────────────────
    def start_subject(self, subject_id):
        started = super().start_subject(subject_id)
        if started and self.active_session is not None:
            self._queue(
                "start_subject",
                {
                    "subject_uid": self._uid("tasks", subject_id),
                    "uid": self._uid("sessions", self.active_session.id),
                },
            )
        return started

    def stop_active_subject(self, end_time=None):
        session = self.active_session
        session_id = session.id if session else None
        # Pin the end time and use the SAME value locally and in the operation.
        #
        # Letting the server fall back to its own "now" is a real divergence: the
        # operation may sit in the outbox for hours, and the sub-30-second rule
        # would then fire here but not there — the Mac would drop the session
        # while the server kept a multi-hour one.
        effective_end = end_time or datetime.now()
        super().stop_active_subject(effective_end)
        if session_id is not None:
            stopped = self.db.get_session(session_id)
            self._queue(
                "stop_active_subject",
                {
                    "end_time": self._iso(
                        stopped.end_time if stopped else effective_end
                    )
                },
            )

    def switch_subject(self, subject_id):
        switched = super().switch_subject(subject_id)
        if switched:
            self._queue(
                "switch_subject", {"subject_uid": self._uid("tasks", subject_id)}
            )
        return switched

    def heartbeat_active_session(self, moment=None):
        # Deliberately NOT queued. It fires roughly once a minute, and an offline
        # spell would fill the outbox with thousands of them. The stop operation
        # carries the real end time, which is what the data actually needs.
        super().heartbeat_active_session(moment)

    # ── sessions ────────────────────────────────────────────────────────
    def add_session(self, subject_id, start_time, end_time):
        session = super().add_session(subject_id, start_time, end_time)
        if session is not None:
            self._queue(
                "add_session",
                {
                    "subject_uid": self._uid("tasks", subject_id),
                    "start_time": self._iso(start_time),
                    "end_time": self._iso(end_time),
                    "uid": self._uid("sessions", session.id),
                },
            )
        return session

    def update_session(self, session_id, subject_id, start_time, end_time):
        session = super().update_session(session_id, subject_id, start_time, end_time)
        if session is not None:
            self._queue(
                "update_session",
                {
                    "session_uid": self._uid("sessions", session_id),
                    "subject_uid": self._uid("tasks", subject_id),
                    "start_time": self._iso(start_time),
                    "end_time": self._iso(end_time),
                },
            )
        return session

    def delete_session(self, session_id):
        uid = self._uid("sessions", session_id)
        super().delete_session(session_id)
        if uid:
            self._queue("delete_session", {"session_uid": uid})

    def duplicate_session(self, session_id, to="today"):
        source_uid = self._uid("sessions", session_id)
        session = super().duplicate_session(session_id, to=to)
        if session is not None and source_uid:
            self._queue(
                "duplicate_session",
                {
                    "session_uid": source_uid,
                    "to": to,
                    "uid": self._uid("sessions", session.id),
                },
            )
        return session

    def shift_session(self, session_id, seconds):
        uid = self._uid("sessions", session_id)
        session = super().shift_session(session_id, seconds)
        if session is not None and uid:
            self._queue("shift_session", {"session_uid": uid, "seconds": int(seconds)})
        return session

    # ── goals ───────────────────────────────────────────────────────────
    def add_todo_task(self, name, notes, deadline):
        goal = super().add_todo_task(name, notes, deadline)
        if goal is not None:
            self._queue(
                "add_goal",
                {
                    "name": goal.name,
                    "notes": goal.notes,
                    "deadline": goal.deadline,
                    "uid": self._uid("todo_tasks", goal.id),
                },
            )
        return goal

    def update_todo_task(self, todo_task_id, name, notes, deadline):
        goal = super().update_todo_task(todo_task_id, name, notes, deadline)
        if goal is not None:
            self._queue(
                "update_goal",
                {
                    "goal_uid": self._uid("todo_tasks", todo_task_id),
                    "name": goal.name,
                    "notes": goal.notes,
                    "deadline": goal.deadline,
                },
            )
        return goal

    def delete_todo_task(self, todo_task_id):
        uid = self._uid("todo_tasks", todo_task_id)
        super().delete_todo_task(todo_task_id)
        if uid:
            self._queue("delete_goal", {"goal_uid": uid})

    def complete_goal(self, goal_id):
        uid = self._uid("todo_tasks", goal_id)
        completed = super().complete_goal(goal_id)
        # Only queue what actually succeeded here: the server enforces the same
        # milestone gate, so a refused completion would just block the queue.
        if completed and uid:
            self._queue("complete_goal", {"goal_uid": uid})
        return completed

    def uncomplete_goal(self, goal_id):
        uid = self._uid("todo_tasks", goal_id)
        super().uncomplete_goal(goal_id)
        if uid:
            self._queue("uncomplete_goal", {"goal_uid": uid})

    def toggle_goal_focused(self, goal_id):
        uid = self._uid("todo_tasks", goal_id)
        focused = super().toggle_goal_focused(goal_id)
        if uid:
            self._queue("toggle_goal_focused", {"goal_uid": uid})
        return focused

    def set_todo_task_order(self, ordered_ids, completed=False):
        super().set_todo_task_order(ordered_ids, completed=completed)
        self._queue(
            "set_goal_order",
            {
                "goal_uids": self._uids("todo_tasks", ordered_ids),
                "completed": bool(completed),
            },
        )

    # ── milestones ──────────────────────────────────────────────────────
    def add_milestone(self, goal_id, title, note=""):
        milestone = super().add_milestone(goal_id, title, note)
        if milestone is not None:
            self._queue(
                "add_milestone",
                {
                    "goal_uid": self._uid("todo_tasks", goal_id),
                    "title": milestone.title,
                    "note": milestone.note,
                    "uid": self._uid("milestones", milestone.id),
                },
            )
        return milestone

    def update_milestone(self, milestone_id, title, note=""):
        milestone = super().update_milestone(milestone_id, title, note)
        if milestone is not None:
            self._queue(
                "update_milestone",
                {
                    "milestone_uid": self._uid("milestones", milestone_id),
                    "title": milestone.title,
                    "note": milestone.note,
                },
            )
        return milestone

    def set_milestone_done(self, milestone_id, done):
        uid = self._uid("milestones", milestone_id)
        super().set_milestone_done(milestone_id, done)
        if uid:
            self._queue(
                "set_milestone_done", {"milestone_uid": uid, "done": bool(done)}
            )

    def delete_milestone(self, milestone_id):
        uid = self._uid("milestones", milestone_id)
        super().delete_milestone(milestone_id)
        if uid:
            self._queue("delete_milestone", {"milestone_uid": uid})

    def set_milestone_order(self, goal_id, ordered_ids):
        super().set_milestone_order(goal_id, ordered_ids)
        self._queue(
            "set_milestone_order",
            {
                "goal_uid": self._uid("todo_tasks", goal_id),
                "milestone_uids": self._uids("milestones", ordered_ids),
            },
        )

    # ── recurring templates ─────────────────────────────────────────────
    def add_goal_template(
        self, title, notes, recurrence, milestone_titles=None, recurrence_day=None
    ):
        template = super().add_goal_template(
            title, notes, recurrence, milestone_titles, recurrence_day
        )
        if template is not None:
            self._queue(
                "add_template",
                {
                    "title": template.title,
                    "notes": template.notes,
                    "recurrence": template.recurrence,
                    "recurrence_day": template.recurrence_day,
                    "milestone_titles": list(milestone_titles or []),
                    "uid": self._uid("goal_templates", template.id),
                },
            )
        return template

    def update_goal_template(
        self, template_id, title, notes, recurrence,
        milestone_titles=None, recurrence_day=None,
    ):
        template = super().update_goal_template(
            template_id, title, notes, recurrence, milestone_titles, recurrence_day
        )
        if template is not None:
            self._queue(
                "update_template",
                {
                    "template_uid": self._uid("goal_templates", template_id),
                    "title": template.title,
                    "notes": template.notes,
                    "recurrence": template.recurrence,
                    "recurrence_day": template.recurrence_day,
                    "milestone_titles": list(milestone_titles or []),
                },
            )
        return template

    def set_goal_template_active(self, template_id, active):
        uid = self._uid("goal_templates", template_id)
        super().set_goal_template_active(template_id, active)
        if uid:
            self._queue(
                "set_template_active", {"template_uid": uid, "active": bool(active)}
            )

    def delete_goal_template(self, template_id):
        uid = self._uid("goal_templates", template_id)
        super().delete_goal_template(template_id)
        if uid:
            self._queue("delete_template", {"template_uid": uid})

    def generate_due_goal_instances(self, now=None):
        """Ask the server to generate; do not generate locally.

        If both machines generated their own instances for the same period, each
        would create a goal and each would mark the template generated, and the
        two would disagree forever. One generator, pulled by everyone.

        Queued at most once at a time — this is called on every launch and every
        time the Goals tab opens, and an offline week should not leave hundreds
        of identical requests behind.
        """
        if not self._has_queued("generate_due_goals"):
            self._queue("generate_due_goals", {})
        return []

    def _has_queued(self, op_name: str) -> bool:
        cur = self.db.connection.cursor()
        cur.execute(
            "SELECT 1 FROM sync_outbox WHERE op_name = ? LIMIT 1", (op_name,)
        )
        return cur.fetchone() is not None

    # ── settings ────────────────────────────────────────────────────────
    def set_setting(self, key, value):
        super().set_setting(key, value)
        # Only genuine preferences travel. Theme, graph range and the like stay
        # on the machine that set them.
        if key in sync_policy.SYNCED_SETTING_KEYS:
            self._queue("set_setting", {"key": key, "value": str(value)})

    def set_day_start(self, value):
        parsed = super().set_day_start(value)
        self._queue(
            "set_setting",
            {"key": "day_start_time", "value": self.db.get_setting("day_start_time")},
        )
        return parsed
