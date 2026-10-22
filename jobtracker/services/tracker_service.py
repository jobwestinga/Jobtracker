"""
Business-logic layer between UI and database.

- Subjects: timed entries with sessions
- Tasks: completable todo items with optional deadlines

Time handling: ALL parsing / duration / logical-day math lives in
``jobtracker.core.timeutils``. This layer must not re-implement date math.

Database access: the service takes a ``Database`` instance. Production code
passes nothing and gets the shared singleton; tests pass an isolated temp
database so they never touch real user data.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import List, Optional

from ..core.database import Database, db as _global_db
from ..core.models import Subject, Session, TodoTask, Milestone, GoalTemplate
from ..core import timeutils
from .subject_service import SubjectService
from .goal_service import GoalService
from .recurrence_service import RecurrenceService
from .session_service import SessionService
from .analytics_service import AnalyticsService, _threshold_period_days
from .timer_service import TimerService


class TrackerService:
    def __init__(
        self,
        database: Optional[Database] = None,
        *,
        device_id: Optional[str] = None,
        recover: bool = True,
    ) -> None:
        """``recover`` runs crash recovery, which CLOSES sessions and so must
        happen exactly once per process — at app launch or server start, never
        per request. Pass ``recover=False`` when constructing a service that is
        only answering a query."""
        self.db: Database = database if database is not None else _global_db
        self.device_id: str = device_id or self.db.get_or_create_device_id()
        self.active_session: Optional[Session] = None
        self.active_subject: Optional[Subject] = None
        # Components share this facade; nested mutations retain subclass
        # dispatch, which is how SyncedTrackerService keeps its outbox complete.
        self._subjects = SubjectService(self)
        self._goals = GoalService(self)
        self._recurrence = RecurrenceService(self)
        self._sessions = SessionService(self)
        self._analytics = AnalyticsService(self)
        self._timer = TimerService(self)

        if recover:
            self.recover_open_sessions()
        else:
            self._adopt_open_session()

    # ── Crash recovery ──────────────────────────────────────────────────
    def _owned_open_sessions(self) -> list[Session]:
        """Open sessions this install is allowed to touch, newest first.

        A session stamped with ANOTHER device's id belongs to that device's
        timer — the phone's running session must survive the Mac starting up.
        A NULL device_id means "no owner recorded" (historical rows, imported
        backups, sessions from before this column existed), which this install
        adopts so old data keeps recovering exactly as it used to.
        """
        return self._timer._owned_open_sessions()

    def _adopt_open_session(self, *, include_other_devices: bool = False) -> None:
        """Refresh the timer from storage, without closing anything.

        Server reads show the newest timer from any device. Recovery remains
        device-scoped, so displaying a foreign timer never claims ownership.
        """
        return self._timer._adopt_open_session(include_other_devices=include_other_devices)

    def recover_open_sessions(self) -> None:
        """Reconcile sessions left open by a crash. Destructive — see __init__.

        Non-destructive for the primary session: it is resumed, never deleted.
        Sessions owned by another device are left completely alone.
        """
        return self._timer.recover_open_sessions()

    def _set_active_subject(self, subject: Optional[Subject]) -> None:
        return self._timer._set_active_subject(subject)

    # ── Settings (UI goes through the service, not the db directly) ─────
    def get_setting(self, key: str, default: str = "") -> str:
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.db.set_setting(key, value)

    def delete_setting(self, key: str) -> None:
        self.db.delete_setting(key)

    # ── Personal weight (calendar days, independent of tracking days) ───
    def list_weight_measurements(self) -> list[dict]:
        from .weight_service import WeightService

        return WeightService(self.db).list_measurements()

    def upsert_weight(self, measured_at, weight_kg, *, weight_uid=None) -> dict:
        from .weight_service import WeightService

        return WeightService(self.db).upsert(
            measured_at, weight_kg, weight_uid=weight_uid
        )

    def delete_weight(self, weight_uid) -> bool:
        from .weight_service import WeightService

        return WeightService(self.db).delete(weight_uid)

    def get_earliest_session_date(self) -> Optional[str]:
        return self._analytics.get_earliest_session_date()

    def get_latest_session_date(self) -> Optional[str]:
        return self._analytics.get_latest_session_date()

    def graph_end_day(self, day_start: Optional[time] = None) -> date:
        """The last logical day any graph should show.

        Normally today, but stretched forward when sessions exist beyond it, so
        work logged ahead of time is visible instead of silently out of range.
        """
        return self._analytics.graph_end_day(day_start)

    # ── Logical day / settings ──────────────────────────────────────────
    def get_day_start(self) -> time:
        """The configured logical-day start time (default 03:00)."""
        return timeutils.parse_day_start(
            self.db.get_setting(
                "day_start_time",
                timeutils.day_start_to_str(timeutils.DEFAULT_DAY_START),
            )
        )

    def set_day_start(self, value) -> time:
        """Persist the logical-day start time. Accepts a ``time`` or 'HH:MM'."""
        parsed = timeutils.parse_day_start(value)
        self.db.set_setting("day_start_time", timeutils.day_start_to_str(parsed))
        return parsed

    # ── Subjects (timed) ────────────────────────────────────────────────
    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        return self._subjects.get_all_subjects(archived)

    def get_last_tracked_subject(self) -> Optional[Subject]:
        return self._subjects.get_last_tracked_subject()

    def get_all_subjects_including_archived(self) -> List[Subject]:
        return self._subjects.get_all_subjects_including_archived()

    def add_subject(self, name: str, color: str, notes: str) -> Optional[Subject]:
        return self._subjects.add_subject(name, color, notes)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        return self._subjects.update_subject(subject_id, name, color, notes)

    def delete_subject(self, subject_id: int) -> None:
        # Stop the timer before deleting the subject row.
        return self._subjects.delete_subject(subject_id)

    def set_subject_order(
        self, ordered_ids: list[int], archived: bool = False
    ) -> None:
        return self._subjects.set_subject_order(ordered_ids, archived)

    def archive_subject(self, subject_id: int) -> None:
        return self._subjects.archive_subject(subject_id)

    def unarchive_subject(self, subject_id: int) -> None:
        return self._subjects.unarchive_subject(subject_id)

    # ── Timer (subjects) ────────────────────────────────────────────────
    def start_subject(
        self, subject_id: int, start_time: Optional[datetime] = None,
        *, allow_parallel: bool = False,
    ) -> bool:
        """Start at the captured client time, preserving offline work.

        ``allow_parallel`` is for authoritative replay of an already recorded
        client session. Normal interactive starts still require an idle timer.
        """
        return self._timer.start_subject(subject_id, start_time, allow_parallel=allow_parallel)

    def _persisted_active_session(self) -> Optional[Session]:
        """Resolve the exact cached timer after a sync or another writer.

        A full mirror refresh can change local integer ids. The uid survives;
        using only the old id can overwrite an unrelated, newly inserted row.
        """
        return self._timer._persisted_active_session()

    def stop_active_subject(self, end_time: Optional[datetime] = None) -> None:
        """Stop the active session. Ends now unless an explicit ``end_time`` is
        given (used by crash-recovery to end at a chosen point in the past)."""
        return self._timer.stop_active_subject(end_time)

    def switch_subject(self, subject_id: int, moment: Optional[datetime] = None) -> bool:
        """Stop the active session (normal stop rules) and start ``subject_id``.

        Refuses to "switch" to the subject already being tracked. Works as a
        plain start when nothing is active.
        """
        return self._timer.switch_subject(subject_id, moment)

    # ── Crash / ghost-time recovery ─────────────────────────────────────
    def get_active_recovery_info(
        self, now: Optional[datetime] = None, gap_threshold_seconds: Optional[int] = None
    ):
        """Return RecoveryInfo if the resumed active session needs a recovery
        prompt (large gap since last known active), else None."""
        return self._timer.get_active_recovery_info(now, gap_threshold_seconds)

    def resolve_recovery(
        self, end_time: datetime, *, session_id: Optional[int] = None
    ) -> None:
        """Close the recovered active session at the user-chosen end time."""
        return self._timer.resolve_recovery(end_time, session_id=session_id)

    def heartbeat_active_session(self, moment: Optional[datetime] = None) -> None:
        """Record that the active session is still legitimately running.

        Called ~once per minute by the UI. Cheap single UPDATE, no history.
        Safe no-op when nothing is being tracked.
        """
        return self._timer.heartbeat_active_session(moment)

    # ── Stats (subjects) ────────────────────────────────────────────────
    def _stats_since_iso(self, filter_type: str) -> Optional[str]:
        """Window start for a card stats filter; None means all time."""
        return self._analytics._stats_since_iso(filter_type)

    def _live_stats_seconds(self, subject_id: int, since_iso: Optional[str]) -> int:
        """Elapsed seconds of the running session if it belongs to this subject
        and starts inside the window (start attribution, like the bar chart)."""
        return self._analytics._live_stats_seconds(subject_id, since_iso)

    def get_subject_stats(self, subject_id: int, filter_type: str = "Total") -> int:
        return self._analytics.get_subject_stats(subject_id, filter_type)

    def get_subject_stats_map(self, filter_type: str = "Total") -> dict[int, int]:
        """Totals for all subjects at once (one query + the live session)."""
        return self._analytics.get_subject_stats_map(filter_type)

    def _elapsed_active_seconds(self, reference: Optional[datetime] = None) -> int:
        """Seconds the active session has been running up to ``reference`` (now)."""
        return self._analytics._elapsed_active_seconds(reference)

    def get_daily_subject_breakdown(
        self, days: int | None = 10, day_start: time | None = None
    ) -> list[dict]:
        """Backwards-compatible daily breakdown. See get_subject_breakdown."""
        return self._analytics.get_daily_subject_breakdown(days, day_start)

    def _resolve_logical_window(
        self,
        day_start: time,
        days: int | None,
        start_date: date | None,
        end_date: date | None,
        grouping: str = "daily",
    ) -> tuple[date, date]:
        """Resolve the [start_day, end_day] logical-day window for analytics.

        Windows are always ANCHORED on the real today, then stretched forward
        to ``last_day`` when sessions are scheduled beyond it. Anchoring on the
        stretched day instead would slide the whole window into the future and
        silently drop real past days from view.
        """
        return self._analytics._resolve_logical_window(day_start, days, start_date, end_date, grouping)

    def get_subject_breakdown(
        self,
        grouping: str = "daily",
        days: int | None = 10,
        day_start: time | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict]:
        """Return stacked work data grouped daily / weekly / monthly.

        Every session is attributed to the logical day of its START time (default
        boundary 03:00), then rolled up into the requested ``grouping`` bucket
        (day, ISO-Monday week, or month). A 23:00 -> 02:00 session counts on the
        day it began. The currently running session is included as a live segment.

        Window selection (in priority order):
          • explicit ``start_date``/``end_date`` (custom range)
          • last ``days`` logical days
          • all time (``days=None``)
        """
        return self._analytics.get_subject_breakdown(grouping, days, day_start, start_date, end_date)

    def get_agenda_data(
        self,
        start_day: date,
        end_day: date,
        day_start: time | None = None,
    ) -> tuple[list[str], list[dict]]:
        """Return (logical_day_keys, sessions) for the agenda timeline.

        Each session carries ``start_h`` / ``end_h`` as agenda hours within its
        logical day (see :func:`timeutils.agenda_hour`): after-midnight work that
        belongs to the previous logical day lands at 24..27 so it renders at the
        bottom of that day rather than padding the top. Sessions that run past the
        logical-day end are clamped to the day's lower edge.
        """
        return self._analytics.get_agenda_data(start_day, end_day, day_start)

    def get_subject_deletion_summary(self, subject_id: int) -> dict:
        """Facts needed to warn before deleting a subject with history."""
        return self._sessions.get_subject_deletion_summary(subject_id)

    # ── Sessions (subjects) ─────────────────────────────────────────────
    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        return self._sessions.get_sessions_for_subject(subject_id)

    def get_session(self, session_id: int) -> Optional[Session]:
        return self._sessions.get_session(session_id)

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Session]:
        return self._sessions.add_session(subject_id, start_time, end_time)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Session]:
        return self._sessions.update_session(session_id, subject_id, start_time, end_time)

    def shift_session(self, session_id: int, seconds: int) -> Optional[Session]:
        """Move a closed session in time by ``seconds`` (negative = earlier).

        Start and end move together, so the duration and the subject are
        untouched. Used by the ±15m / ±1h nudge buttons.
        """
        return self._sessions.shift_session(session_id, seconds)

    def delete_session(self, session_id: int) -> None:
        return self._sessions.delete_session(session_id)

    def duplicate_session(
        self, session_id: int, to: str = "today"
    ) -> Optional[Session]:
        """Copy a closed session onto another logical day, same clock time.

        ``to="today"``    -> the current logical day.
        ``to="next_day"`` -> the logical day after the original's.

        Duration and subject are preserved; only the date moves. Copies that
        land in the future are allowed on purpose (planning ahead is a valid
        use), so the only failures are a missing, open, or unparseable session.
        """
        return self._sessions.duplicate_session(session_id, to)

    # ── Todo Tasks (completable) ────────────────────────────────────────
    def get_all_todo_tasks(self) -> List[TodoTask]:
        return self._goals.get_all_todo_tasks()

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        return self._goals.add_todo_task(name, notes, deadline)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        return self._goals.update_todo_task(todo_task_id, name, notes, deadline)

    def delete_todo_task(self, todo_task_id: int) -> None:
        return self._goals.delete_todo_task(todo_task_id)

    def set_todo_task_order(
        self, ordered_ids: list[int], completed: bool = False
    ) -> None:
        return self._goals.set_todo_task_order(ordered_ids, completed)

    # ── Goals (the redesigned Tasks tab) ────────────────────────────────
    # A "Goal" is a todo_tasks row. These are convenience aliases plus the
    # milestone-aware completion rules.
    def get_active_goals(self) -> List[TodoTask]:
        return self._goals.get_active_goals()

    def get_completed_goals(self) -> List[TodoTask]:
        return self._goals.get_completed_goals()

    def get_goal(self, goal_id: int) -> Optional[TodoTask]:
        return self._goals.get_goal(goal_id)

    def get_goal_milestones(self, goal_id: int) -> List[Milestone]:
        return self._goals.get_goal_milestones(goal_id)

    def get_goal_progress(self, goal_id: int) -> tuple[int, int]:
        """(milestones_done, milestones_total) for a goal."""
        return self._goals.get_goal_progress(goal_id)

    def can_complete_goal(self, goal_id: int) -> bool:
        """A goal may be completed only when all milestones are done, or when it
        has no milestones at all (then manual completion is always allowed)."""
        return self._goals.can_complete_goal(goal_id)

    def complete_goal(self, goal_id: int) -> bool:
        """Mark a goal complete. Refuses if milestones remain unchecked."""
        return self._goals.complete_goal(goal_id)

    def uncomplete_goal(self, goal_id: int) -> None:
        return self._goals.uncomplete_goal(goal_id)

    def toggle_goal_focused(self, goal_id: int) -> bool:
        """Flip the weekly-focus flag. Returns the new state (False if the
        goal does not exist)."""
        return self._goals.toggle_goal_focused(goal_id)

    # Milestone editing
    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self._goals.add_milestone(goal_id, title, note)

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self._goals.update_milestone(milestone_id, title, note)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        return self._goals.set_milestone_done(milestone_id, done)

    def delete_milestone(self, milestone_id: int) -> None:
        return self._goals.delete_milestone(milestone_id)

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        return self._goals.set_milestone_order(goal_id, ordered_ids)

    # ── Recurring goal templates ────────────────────────────────────────
    @staticmethod
    def build_milestones_json(titles: list) -> str:
        """Serialize milestone specs (strings or {title,note}) for a template."""
        return RecurrenceService.build_milestones_json(titles)

    def get_goal_templates(self, active_only: bool = False) -> List[GoalTemplate]:
        return self._recurrence.get_goal_templates(active_only)

    def add_goal_template(
        self, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        return self._recurrence.add_goal_template(title, notes, recurrence, milestone_titles, recurrence_day)

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        return self._recurrence.update_goal_template(template_id, title, notes, recurrence, milestone_titles, recurrence_day)

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        return self._recurrence.set_goal_template_active(template_id, active)

    def delete_goal_template(self, template_id: int) -> None:
        return self._recurrence.delete_goal_template(template_id)

    @staticmethod
    def _template_period_key(recurrence: str, logical: date) -> str:
        """The logical-period key a template generates against. Distinct keys ->
        a new generation is due; identical key -> already generated."""
        return RecurrenceService._template_period_key(recurrence, logical)

    @staticmethod
    def _template_instance_title(title: str, logical: date) -> str:
        """Resolve supported placeholders in a generated goal title."""
        return RecurrenceService._template_instance_title(title, logical)

    @staticmethod
    def _template_is_due(template: GoalTemplate, logical: date) -> bool:
        return RecurrenceService._template_is_due(template, logical)

    def generate_due_goal_instances(self, now: Optional[datetime] = None) -> list[int]:
        """Append a Goal instance to the top of the list for any active template
        whose current logical period hasn't been generated yet. Idempotent within
        a period (safe to call on every launch). Returns the new goal ids.

        Uncompleted goals from previous periods are never touched — they simply
        remain in the list. Generation is purely additive: nothing is ever
        auto-removed, so a skipped day stays visible as a backlog item.
        """
        return self._recurrence.generate_due_goal_instances(now)

    def get_sessions_for_logical_day(
        self, day: date, day_start: time | None = None
    ) -> list[dict]:
        """All closed sessions belonging to a single logical day, with subject
        metadata. Used when a day is opened from a graph."""
        return self._analytics.get_sessions_for_logical_day(day, day_start)

    # ── Backup ──────────────────────────────────────────────────────────
    def export_data(self) -> dict:
        return self.db.export_data()

    def import_data(self, data: dict) -> None:
        self.db.import_data(data)
