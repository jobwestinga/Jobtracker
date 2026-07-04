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

import calendar
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from ..core.database import Database, db as _global_db
from ..core.models import Subject, Session, TodoTask, Milestone, GoalTemplate
from ..core import timeutils, recovery

logger = logging.getLogger("jobtracker")


def _threshold_period_days(
    grouping: str,
    bucket_date: date,
    today: date,
) -> int:
    """Days used only for a bar's color threshold, not its displayed total."""
    if grouping == "weekly":
        if bucket_date == timeutils.week_start(today):
            return today.weekday() + 1
        return 7
    if grouping == "monthly":
        if (
            bucket_date.year == today.year
            and bucket_date.month == today.month
        ):
            return today.day
        return calendar.monthrange(bucket_date.year, bucket_date.month)[1]
    return 1


class TrackerService:
    def __init__(self, database: Optional[Database] = None) -> None:
        self.db: Database = database if database is not None else _global_db

        # Recover from crashes that left one or more sessions open.
        open_sessions = self.db.get_open_sessions()
        self.active_session: Optional[Session] = open_sessions[0] if open_sessions else None
        self.active_subject: Optional[Subject] = None

        # Close stale parallel open sessions to keep a single active timer
        # invariant. The newest open session is kept as active. Any unexpected
        # extras are closed but explicitly preserved even when shorter than the
        # normal 30-second minimum: unfinished recovery data is never deleted.
        if len(open_sessions) > 1:
            now = datetime.now()
            for stale in open_sessions[1:]:
                if stale.id is None:
                    continue
                started_at = timeutils.parse_iso(stale.start_time)
                last_active = timeutils.parse_iso(stale.last_active_at) or started_at
                if started_at is None:
                    end_at = now
                else:
                    end_at = min(now, max(started_at, last_active or started_at))
                duration = timeutils.duration_seconds(started_at, end_at)
                self.db.close_recovered_session(stale.id, end_at, duration)
                logger.info(
                    "Recovery: closed stale parallel open session %s at its "
                    "last-known-active time (%ds)",
                    stale.id,
                    duration,
                )

        if self.active_session:
            self.active_subject = self.db.get_subject(self.active_session.subject_id)
            # If the subject was deleted while a session was open, close it.
            if self.active_subject is None:
                if self.active_session.id is not None:
                    self.db.close_recovered_session(
                        self.active_session.id, datetime.now(), 0
                    )
                logger.info("Recovery: active session had no subject; closed it")
                self.active_session = None
            else:
                logger.info(
                    "Recovery: resumed active session %s for subject '%s'",
                    self.active_session.id, self.active_subject.name,
                )

    def _set_active_subject(self, subject: Optional[Subject]) -> None:
        self.active_subject = subject

    # ── Settings (UI goes through the service, not the db directly) ─────
    def get_setting(self, key: str, default: str = "") -> str:
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.db.set_setting(key, value)

    def delete_setting(self, key: str) -> None:
        self.db.delete_setting(key)

    def get_earliest_session_date(self) -> Optional[str]:
        return self.db.get_earliest_session_date()

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
        return self.db.get_all_subjects(archived=archived)

    def get_all_subjects_including_archived(self) -> List[Subject]:
        return self.db.get_all_subjects_including_archived()

    def add_subject(self, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        if self.db.get_subject_by_name(normalized_name):
            return None
        return self.db.add_subject(normalized_name, normalized_color, normalized_notes)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        existing = self.db.get_subject_by_name(normalized_name)
        if existing and existing.id != subject_id:
            return None
        return self.db.update_subject(subject_id, normalized_name, normalized_color, normalized_notes)

    def delete_subject(self, subject_id: int) -> None:
        # Stop the timer before deleting the subject row.
        if self.active_subject and self.active_subject.id == subject_id:
            self.stop_active_subject()
        self.db.delete_subject(subject_id)  # CASCADE deletes sessions too

    def set_subject_order(
        self, ordered_ids: list[int], archived: bool = False
    ) -> None:
        self.db.set_subject_order(ordered_ids, archived=archived)

    def archive_subject(self, subject_id: int) -> None:
        if self.active_subject and self.active_subject.id == subject_id:
            self.stop_active_subject()
        self.db.archive_subject(subject_id)

    def unarchive_subject(self, subject_id: int) -> None:
        self.db.unarchive_subject(subject_id)

    # ── Timer (subjects) ────────────────────────────────────────────────
    def start_subject(self, subject_id: int) -> bool:
        if self.active_session:
            return False
        try:
            self.active_session = self.db.start_session(subject_id)
        except ValueError:
            return False
        if self.active_session.id is None:
            self.active_session = None
            return False
        self._set_active_subject(self.db.get_subject(subject_id))
        if self.active_subject is None:
            self.db.stop_session(self.active_session.id, datetime.now(), 0)
            self.active_session = None
            return False
        return True

    def stop_active_subject(self, end_time: Optional[datetime] = None) -> None:
        """Stop the active session. Ends now unless an explicit ``end_time`` is
        given (used by crash-recovery to end at a chosen point in the past)."""
        if not self.active_session:
            return
        if self.active_session.id is None:
            self.active_session = None
            self._set_active_subject(None)
            return
        end_dt = end_time or datetime.now()
        start_dt = timeutils.parse_iso(self.active_session.start_time)
        duration = timeutils.duration_seconds(start_dt, end_dt)
        self.db.stop_session(self.active_session.id, end_dt, duration)
        self.active_session = None
        self._set_active_subject(None)

    def switch_subject(self, subject_id: int) -> bool:
        """Stop the active session (normal stop rules) and start ``subject_id``.

        Refuses to "switch" to the subject already being tracked. Works as a
        plain start when nothing is active.
        """
        if self.active_subject and self.active_subject.id == subject_id:
            return False
        if self.db.get_subject(subject_id) is None:
            return False
        if self.active_session:
            self.stop_active_subject()
        return self.start_subject(subject_id)

    # ── Crash / ghost-time recovery ─────────────────────────────────────
    def get_active_recovery_info(
        self, now: Optional[datetime] = None, gap_threshold_seconds: Optional[int] = None
    ):
        """Return RecoveryInfo if the resumed active session needs a recovery
        prompt (large gap since last known active), else None."""
        if not self.active_session or not self.active_subject:
            return None
        threshold = (
            recovery.DEFAULT_GAP_PROMPT_SECONDS
            if gap_threshold_seconds is None
            else gap_threshold_seconds
        )
        return recovery.build_recovery_info(
            self.active_session,
            self.active_subject.name,
            now=now,
            gap_threshold_seconds=threshold,
        )

    def resolve_recovery(self, end_time: datetime) -> None:
        """Close the recovered active session at the user-chosen end time."""
        logger.info(
            "Recovery resolved: ending session %s at %s",
            self.active_session.id if self.active_session else None,
            end_time.isoformat(),
        )
        self.stop_active_subject(end_time=end_time)

    def heartbeat_active_session(self, moment: Optional[datetime] = None) -> None:
        """Record that the active session is still legitimately running.

        Called ~once per minute by the UI. Cheap single UPDATE, no history.
        Safe no-op when nothing is being tracked.
        """
        if not self.active_session or self.active_session.id is None:
            return
        when_iso = timeutils.to_iso(moment) if moment is not None else timeutils.now_iso()
        self.db.touch_active_session(self.active_session.id, when_iso)
        self.active_session.last_active_at = when_iso

    # ── Stats (subjects) ────────────────────────────────────────────────
    def _stats_since_iso(self, filter_type: str) -> Optional[str]:
        """Window start for a card stats filter; None means all time."""
        now = datetime.now()
        if filter_type == "Today":
            # "Today" honours the configurable logical-day boundary (default 03:00).
            day_start = self.get_day_start()
            today = timeutils.logical_day(now, day_start)
            start_dt, _ = timeutils.logical_day_bounds(today, day_start)
            return start_dt.isoformat()
        if filter_type == "Last 7 days":
            return (now - timedelta(days=7)).isoformat()
        if filter_type == "Last 30 days":
            return (now - timedelta(days=30)).isoformat()
        return None

    def _live_stats_seconds(self, subject_id: int, since_iso: Optional[str]) -> int:
        """Elapsed seconds of the running session if it belongs to this subject
        and starts inside the window (start attribution, like the bar chart)."""
        if not self.active_session or self.active_session.subject_id != subject_id:
            return 0
        if since_iso and self.active_session.start_time < since_iso:
            return 0
        return self._elapsed_active_seconds()

    def get_subject_stats(self, subject_id: int, filter_type: str = "Total") -> int:
        since_iso = self._stats_since_iso(filter_type)
        total = self.db.get_subject_stats(subject_id, since_iso)
        return total + self._live_stats_seconds(subject_id, since_iso)

    def get_subject_stats_map(self, filter_type: str = "Total") -> dict[int, int]:
        """Totals for all subjects at once (one query + the live session)."""
        since_iso = self._stats_since_iso(filter_type)
        totals = self.db.get_all_subject_stats(since_iso)
        if self.active_session:
            live_id = self.active_session.subject_id
            live = self._live_stats_seconds(live_id, since_iso)
            if live:
                totals[live_id] = totals.get(live_id, 0) + live
        return totals

    def _elapsed_active_seconds(self, reference: Optional[datetime] = None) -> int:
        """Seconds the active session has been running up to ``reference`` (now)."""
        if not self.active_session:
            return 0
        started_at = timeutils.parse_iso(self.active_session.start_time)
        if started_at is None:
            return 0
        return timeutils.duration_seconds(started_at, reference or datetime.now())

    def get_daily_subject_breakdown(
        self, days: int | None = 10, day_start: time | None = None
    ) -> list[dict]:
        """Backwards-compatible daily breakdown. See get_subject_breakdown."""
        return self.get_subject_breakdown(
            grouping="daily", days=days, day_start=day_start
        )

    def _resolve_logical_window(
        self,
        day_start: time,
        days: int | None,
        start_date: date | None,
        end_date: date | None,
        grouping: str = "daily",
    ) -> tuple[date, date]:
        """Resolve the [start_day, end_day] logical-day window for analytics."""
        today = timeutils.logical_day(datetime.now(), day_start)
        if start_date is not None and end_date is not None:
            start_day, end_day = start_date, end_date
            if end_day < start_day:
                start_day, end_day = end_day, start_day
        elif days is not None and grouping == "weekly":
            # Presets represent whole visible buckets: 7 days -> one week,
            # 14 days -> two weeks. This avoids a rolling seven-day window
            # producing two partial weekly bars.
            periods = max(1, (days + 6) // 7)
            end_day = today
            start_day = timeutils.week_start(today) - timedelta(weeks=periods - 1)
        elif days is not None and grouping == "monthly":
            # Likewise, short rolling ranges should not straddle two month bars.
            periods = max(1, (days + 29) // 30)
            end_day = today
            start_day = timeutils.month_start(today)
            for _ in range(periods - 1):
                start_day = (start_day - timedelta(days=1)).replace(day=1)
        elif days is not None:
            end_day = today
            start_day = today - timedelta(days=max(1, days) - 1)
        else:  # "All time"
            earliest = timeutils.logical_day_of_iso(
                self.db.get_earliest_session_date(), day_start
            )
            if self.active_session:
                active_day = timeutils.logical_day_of_iso(
                    self.active_session.start_time, day_start
                )
                if active_day is not None and (earliest is None or active_day < earliest):
                    earliest = active_day
            start_day = earliest or today
            end_day = today
        return start_day, end_day

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
        day_start = day_start or self.get_day_start()
        now = datetime.now()

        start_day, end_day = self._resolve_logical_window(
            day_start, days, start_date, end_date, grouping
        )

        # Ordered, de-duplicated bucket keys spanning the window.
        bucket_keys: list[str] = []
        seen: set[str] = set()
        for logical in timeutils.logical_day_range(start_day, end_day):
            key = timeutils.bucket_key(logical, grouping).isoformat()
            if key not in seen:
                seen.add(key)
                bucket_keys.append(key)

        buckets: dict[str, list[dict]] = {key: [] for key in bucket_keys}
        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}

        first_start_dt, _ = timeutils.logical_day_bounds(start_day, day_start)
        _, last_end_dt = timeutils.logical_day_bounds(end_day, day_start)
        sessions = self.db.get_all_closed_sessions_in_range(
            first_start_dt.isoformat(), last_end_dt.isoformat()
        )

        def _bucket_for(start_iso: str) -> str | None:
            logical = timeutils.logical_day_of_iso(start_iso, day_start)
            if logical is None:
                return None
            key = timeutils.bucket_key(logical, grouping).isoformat()
            return key if key in buckets else None

        for sess in sessions:
            key = _bucket_for(sess.start_time)
            if key is None:
                continue
            subject = subjects.get(sess.subject_id)
            if not subject or sess.duration_seconds <= 0:
                continue
            buckets[key].append(
                {
                    "subject_id": sess.subject_id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "seconds": sess.duration_seconds,
                    "start_time": sess.start_time,
                    "end_time": sess.end_time,
                }
            )

        # Include the currently running session as a live segment.
        if self.active_session:
            subject = subjects.get(self.active_session.subject_id)
            key = _bucket_for(self.active_session.start_time)
            if subject is not None and key is not None:
                live_seconds = self._elapsed_active_seconds(now)
                if live_seconds > 0:
                    buckets[key].append(
                        {
                            "subject_id": self.active_session.subject_id,
                            "subject_name": subject.name,
                            "color": subject.color,
                            "seconds": live_seconds,
                            "start_time": self.active_session.start_time,
                            "end_time": now.isoformat(),
                        }
                    )

        result = []
        for key in bucket_keys:
            total_seconds = sum(segment["seconds"] for segment in buckets[key])
            bucket_date = date.fromisoformat(key)
            if grouping == "weekly":
                period_days = 7
            elif grouping == "monthly":
                period_days = calendar.monthrange(
                    bucket_date.year, bucket_date.month
                )[1]
            else:
                period_days = 1
            threshold_days = _threshold_period_days(
                grouping,
                bucket_date,
                timeutils.logical_day(now, day_start),
            )
            threshold_factor = {
                "weekly": 0.85,
                "monthly": 0.70,
            }.get(grouping, 1.0)
            result.append(
                {
                    "date": key,
                    "total_seconds": total_seconds,
                    # Weekly labels reach each daily intensity tier at 85% of
                    # the straight seven-day threshold; monthly labels at 70%.
                    "intensity_seconds": (
                        total_seconds / (threshold_days * threshold_factor)
                    ),
                    "period_days": period_days,
                    "threshold_days": threshold_days,
                    "threshold_factor": threshold_factor,
                    "segments": buckets[key],
                }
            )
        return result

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
        day_start = day_start or self.get_day_start()
        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}
        day_keys = [d.isoformat() for d in timeutils.logical_day_range(start_day, end_day)]
        day_key_set = set(day_keys)
        day_end_hour = 24.0 + day_start.hour + day_start.minute / 60.0

        first_start_dt, _ = timeutils.logical_day_bounds(start_day, day_start)
        _, last_end_dt = timeutils.logical_day_bounds(end_day, day_start)
        sessions = self.db.get_all_closed_sessions_in_range(
            first_start_dt.isoformat(), last_end_dt.isoformat()
        )

        result: list[dict] = []

        def _emit(session_id, subject, start_dt, end_dt, duration):
            logical = timeutils.logical_day(start_dt, day_start)
            key = logical.isoformat()
            if key not in day_key_set:
                return
            start_h = timeutils.agenda_hour(start_dt, day_start)
            if timeutils.logical_day(end_dt, day_start) > logical:
                end_h = day_end_hour  # spilled past this logical day; clamp to edge
            else:
                end_h = timeutils.agenda_hour(end_dt, day_start)
            if end_h <= start_h:
                end_h = min(day_end_hour, start_h + (1.0 / 60.0))
            result.append(
                {
                    "session_id": session_id,
                    "day": key,
                    "subject_id": subject.id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "start_h": start_h,
                    "end_h": end_h,
                    "duration_seconds": duration,
                }
            )

        for sess in sessions:
            subject = subjects.get(sess.subject_id)
            start_dt = timeutils.parse_iso(sess.start_time)
            end_dt = timeutils.parse_iso(sess.end_time)
            if not subject or start_dt is None or end_dt is None:
                continue
            _emit(sess.id, subject, start_dt, end_dt, sess.duration_seconds)

        if self.active_session:
            subject = subjects.get(self.active_session.subject_id)
            start_dt = timeutils.parse_iso(self.active_session.start_time)
            if subject is not None and start_dt is not None:
                now = datetime.now()
                _emit(None, subject, start_dt, now, self._elapsed_active_seconds(now))

        return day_keys, result

    def get_subject_deletion_summary(self, subject_id: int) -> dict:
        """Facts needed to warn before deleting a subject with history."""
        sessions = self.db.get_sessions_for_subject(subject_id)
        closed = [s for s in sessions if s.end_time]
        total_seconds = sum(s.duration_seconds for s in closed)
        starts = sorted(s.start_time for s in closed if s.start_time)
        return {
            "session_count": len(closed),
            "total_seconds": total_seconds,
            "earliest": starts[0] if starts else None,
            "latest": starts[-1] if starts else None,
            "sessions": closed,
        }

    # ── Sessions (subjects) ─────────────────────────────────────────────
    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        return self.db.get_sessions_for_subject(subject_id)

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        note: Optional[str] = None,
    ) -> Optional[Session]:
        duration = timeutils.duration_seconds(start_time, end_time)
        return self.db.add_session(subject_id, start_time, end_time, duration, note)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        note: Optional[str] = None,
    ) -> Optional[Session]:
        duration = timeutils.duration_seconds(start_time, end_time)
        return self.db.update_session(session_id, subject_id, start_time, end_time, duration, note)

    def delete_session(self, session_id: int) -> None:
        self.db.delete_session(session_id)

    # ── Todo Tasks (completable) ────────────────────────────────────────
    def get_all_todo_tasks(self) -> List[TodoTask]:
        return self.db.get_all_todo_tasks()

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return self.db.add_todo_task(normalized_name, normalized_notes, normalized_deadline)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return self.db.update_todo_task(todo_task_id, normalized_name, normalized_notes, normalized_deadline)

    def delete_todo_task(self, todo_task_id: int) -> None:
        self.db.delete_todo_task(todo_task_id)

    def set_todo_task_order(
        self, ordered_ids: list[int], completed: bool = False
    ) -> None:
        self.db.set_todo_task_order(ordered_ids, completed=completed)

    # ── Goals (the redesigned Tasks tab) ────────────────────────────────
    # A "Goal" is a todo_tasks row. These are convenience aliases plus the
    # milestone-aware completion rules.
    def get_active_goals(self) -> List[TodoTask]:
        self.db.ensure_manual_goal_order()
        return self.db.get_all_todo_tasks()

    def get_completed_goals(self) -> List[TodoTask]:
        return self.db.get_completed_todo_tasks()

    def get_goal(self, goal_id: int) -> Optional[TodoTask]:
        return self.db.get_todo_task(goal_id)

    def get_goal_milestones(self, goal_id: int) -> List[Milestone]:
        return self.db.get_milestones(goal_id)

    def get_goal_progress(self, goal_id: int) -> tuple[int, int]:
        """(milestones_done, milestones_total) for a goal."""
        milestones = self.db.get_milestones(goal_id)
        done = sum(1 for m in milestones if m.is_done)
        return done, len(milestones)

    def can_complete_goal(self, goal_id: int) -> bool:
        """A goal may be completed only when all milestones are done, or when it
        has no milestones at all (then manual completion is always allowed)."""
        if self.db.get_todo_task(goal_id) is None:
            return False
        done, total = self.get_goal_progress(goal_id)
        return total == 0 or done == total

    def complete_goal(self, goal_id: int) -> bool:
        """Mark a goal complete. Refuses if milestones remain unchecked."""
        if not self.can_complete_goal(goal_id):
            return False
        self.db.complete_todo_task(goal_id)
        return True

    def uncomplete_goal(self, goal_id: int) -> None:
        self.db.uncomplete_todo_task(goal_id)

    # Milestone editing
    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        milestone = self.db.add_milestone(goal_id, title, note)
        if milestone is not None:
            goal = self.db.get_todo_task(goal_id)
            if goal and goal.is_completed:
                self.db.uncomplete_todo_task(goal_id)
        return milestone

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self.db.update_milestone(milestone_id, title, note)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        milestone = self.db.get_milestone(milestone_id)
        self.db.set_milestone_done(milestone_id, done)
        # A completed goal may never contain an unchecked milestone. Reopen it
        # automatically so completion remains an honest, reversible invariant.
        if milestone is not None and not done:
            goal = self.db.get_todo_task(milestone.goal_id)
            if goal and goal.is_completed:
                self.db.uncomplete_todo_task(milestone.goal_id)

    def delete_milestone(self, milestone_id: int) -> None:
        self.db.delete_milestone(milestone_id)

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        self.db.set_milestone_order(goal_id, ordered_ids)

    # ── Recurring goal templates ────────────────────────────────────────
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
        return self.db.get_goal_templates(active_only=active_only)

    def add_goal_template(
        self, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        ms_json = self.build_milestones_json(milestone_titles or [])
        return self.db.add_goal_template(
            title, notes, recurrence, ms_json, recurrence_day
        )

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str,
        milestone_titles: Optional[list] = None,
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        ms_json = self.build_milestones_json(milestone_titles or [])
        return self.db.update_goal_template(
            template_id, title, notes, recurrence, ms_json, recurrence_day
        )

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        self.db.set_goal_template_active(template_id, active)

    def delete_goal_template(self, template_id: int) -> None:
        self.db.delete_goal_template(template_id)

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
        remain in the list.
        """
        now = now or datetime.now()
        day_start = self.get_day_start()
        logical = timeutils.logical_day(now, day_start)

        created: list[int] = []
        for tpl in self.db.get_goal_templates(active_only=True):
            if not self._template_is_due(tpl, logical):
                continue
            key = self._template_period_key(tpl.recurrence, logical)
            if tpl.last_generated == key:
                continue  # already generated for this period -> no duplicate

            instance_title = self._template_instance_title(tpl.title, logical)
            goal = self.db.add_todo_task_at_top(
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
                    self.db.add_milestone(goal.id, title, note)

            self.db.set_goal_template_last_generated(tpl.id, key)
            if goal.id is not None:
                created.append(goal.id)
            logger.info(
                "Generated goal '%s' from template %s for %s",
                instance_title,
                tpl.id,
                key,
            )

        return created

    # ── Heatmap (tracked time per logical day) ──────────────────────────
    def get_heatmap_data(
        self,
        day_start: time | None = None,
        days: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict]:
        """Tracked seconds per logical day. Defaults to all history (earliest
        session -> today). Reuses the logical-day daily breakdown."""
        if start_date is not None and end_date is not None:
            breakdown = self.get_subject_breakdown(
                grouping="daily", day_start=day_start,
                start_date=start_date, end_date=end_date,
            )
        else:
            breakdown = self.get_subject_breakdown(
                grouping="daily", days=days, day_start=day_start
            )
        return [
            {"date": d["date"], "total_seconds": d["total_seconds"]}
            for d in breakdown
        ]

    def get_sessions_for_logical_day(
        self, day: date, day_start: time | None = None
    ) -> list[dict]:
        """All closed sessions belonging to a single logical day, with subject
        metadata. Used when a heatmap cell is clicked."""
        day_start = day_start or self.get_day_start()
        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}
        start_dt, end_dt = timeutils.logical_day_bounds(day, day_start)
        rows = self.db.get_all_closed_sessions_in_range(start_dt.isoformat(), end_dt.isoformat())
        result: list[dict] = []
        for sess in rows:
            subject = subjects.get(sess.subject_id)
            if not subject:
                continue
            result.append(
                {
                    "session_id": sess.id,
                    "subject_id": sess.subject_id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "start_time": sess.start_time,
                    "end_time": sess.end_time,
                    "duration_seconds": sess.duration_seconds,
                    "note": sess.note,
                }
            )
        if self.active_session:
            active_start = timeutils.parse_iso(self.active_session.start_time)
            subject = subjects.get(self.active_session.subject_id)
            if (
                active_start is not None
                and subject is not None
                and timeutils.logical_day(active_start, day_start) == day
            ):
                now = datetime.now()
                result.append(
                    {
                        "session_id": None,
                        "subject_id": self.active_session.subject_id,
                        "subject_name": subject.name,
                        "color": subject.color,
                        "start_time": self.active_session.start_time,
                        "end_time": None,
                        "duration_seconds": self._elapsed_active_seconds(now),
                        "note": self.active_session.note,
                    }
                )
        return result

    # ── Backup ──────────────────────────────────────────────────────────
    def export_data(self) -> dict:
        return self.db.export_data()

    def import_data(self, data: dict) -> None:
        self.db.import_data(data)
