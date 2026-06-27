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

import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from ..core.database import Database, db as _global_db
from ..core.models import Subject, Session, TodoTask
from ..core import timeutils

logger = logging.getLogger("jobtracker")


class TrackerService:
    def __init__(self, database: Optional[Database] = None) -> None:
        self.db: Database = database if database is not None else _global_db

        # Recover from crashes that left one or more sessions open.
        open_sessions = self.db.get_open_sessions()
        self.active_session: Optional[Session] = open_sessions[0] if open_sessions else None
        self.active_subject: Optional[Subject] = None

        # Close stale parallel open sessions to keep a single active timer
        # invariant. The newest open session is kept as the active one; any
        # extras are closed out (they should not normally exist).
        if len(open_sessions) > 1:
            now = datetime.now()
            for stale in open_sessions[1:]:
                if stale.id is None:
                    continue
                started_at = timeutils.parse_iso(stale.start_time)
                duration = timeutils.duration_seconds(started_at, now)
                self.db.stop_session(stale.id, now, duration)
                logger.info(
                    "Recovery: closed stale parallel open session %s (%ds)",
                    stale.id, duration,
                )

        if self.active_session:
            self.active_subject = self.db.get_subject(self.active_session.subject_id)
            # If the subject was deleted while a session was open, close it.
            if self.active_subject is None:
                if self.active_session.id is not None:
                    self.db.stop_session(self.active_session.id, datetime.now(), 0)
                logger.info("Recovery: active session had no subject; closed it")
                self.active_session = None
            else:
                logger.info(
                    "Recovery: resumed active session %s for subject '%s'",
                    self.active_session.id, self.active_subject.name,
                )

    def _set_active_subject(self, subject: Optional[Subject]) -> None:
        self.active_subject = subject

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

    def move_subject(self, subject_id: int, direction: int) -> None:
        self.db.move_subject(subject_id, direction)

    def set_subject_order(self, ordered_ids: list[int]) -> None:
        self.db.set_subject_order(ordered_ids)

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

    def stop_active_subject(self) -> None:
        if not self.active_session:
            return
        if self.active_session.id is None:
            self.active_session = None
            self._set_active_subject(None)
            return
        end_dt = datetime.now()
        start_dt = timeutils.parse_iso(self.active_session.start_time)
        duration = timeutils.duration_seconds(start_dt, end_dt)
        self.db.stop_session(self.active_session.id, end_dt, duration)
        self.active_session = None
        self._set_active_subject(None)

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
    def get_subject_stats(self, subject_id: int, filter_type: str = "Total") -> int:
        since_iso = None
        now = datetime.now()
        if filter_type == "Today":
            # "Today" honours the configurable logical-day boundary (default 03:00).
            day_start = self.get_day_start()
            today = timeutils.logical_day(now, day_start)
            start_dt, _ = timeutils.logical_day_bounds(today, day_start)
            since_iso = start_dt.isoformat()
        elif filter_type == "Last 7 days":
            since_iso = (now - timedelta(days=7)).isoformat()
        elif filter_type == "Last 30 days":
            since_iso = (now - timedelta(days=30)).isoformat()
        return self.db.get_subject_stats(subject_id, since_iso)

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
        """Return the last N logical days of stacked work data.

        Sessions are attributed to the logical day of their START time (default
        boundary 03:00). A session 23:00 -> 02:00 therefore counts on the day it
        began rather than being split at midnight. If *days* is ``None`` every
        logical day from the earliest recorded session to today is included.
        """
        day_start = day_start or self.get_day_start()
        now = datetime.now()
        today = timeutils.logical_day(now, day_start)

        if days is None:
            earliest = timeutils.logical_day_of_iso(
                self.db.get_earliest_session_date(), day_start
            )
            start_day = earliest or today
            num_days = max(1, (today - start_day).days + 1)
        else:
            num_days = max(1, days)
            start_day = today - timedelta(days=num_days - 1)

        day_keys = [
            (start_day + timedelta(days=i)).isoformat()
            for i in range(num_days)
        ]

        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}

        buckets: dict[str, list[dict]] = {day: [] for day in day_keys}

        # Query from the calendar start of the earliest logical day shown.
        first_start_dt, _ = timeutils.logical_day_bounds(start_day, day_start)
        sessions = self.db.get_closed_sessions_since(first_start_dt.isoformat())
        for sess in sessions:
            logical = timeutils.logical_day_of_iso(sess.start_time, day_start)
            if logical is None:
                continue
            key = logical.isoformat()
            if key not in buckets:
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
            logical = timeutils.logical_day_of_iso(self.active_session.start_time, day_start)
            subject = subjects.get(self.active_session.subject_id)
            if logical is not None and subject is not None:
                key = logical.isoformat()
                if key in buckets:
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

        output: list[dict] = []
        for day in day_keys:
            segments = buckets[day]  # already in chronological order
            output.append(
                {
                    "date": day,
                    "total_seconds": sum(seg["seconds"] for seg in segments),
                    "segments": segments,
                }
            )
        return output

    def get_sessions_in_range(
        self, since_date: date, until_date: date
    ) -> list[dict]:
        """Return all closed sessions between two CALENDAR dates with subject
        metadata. Used by the agenda timeline, which paints sessions at their
        real clock position per calendar day (so it intentionally uses calendar
        days, not logical days).
        """
        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}
        since_iso = f"{since_date.isoformat()}T00:00:00"
        until_iso = f"{(until_date + timedelta(days=1)).isoformat()}T00:00:00"
        sessions = self.db.get_all_closed_sessions_in_range(since_iso, until_iso)

        result: list[dict] = []
        for sess in sessions:
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
                }
            )

        # Include live session
        if self.active_session:
            started_at = timeutils.parse_iso(self.active_session.start_time)
            subject = subjects.get(self.active_session.subject_id)
            if started_at and subject:
                sess_date = started_at.date()
                if since_date <= sess_date <= until_date:
                    now = datetime.now()
                    result.append(
                        {
                            "session_id": None,
                            "subject_id": self.active_session.subject_id,
                            "subject_name": subject.name,
                            "color": subject.color,
                            "start_time": self.active_session.start_time,
                            "end_time": now.isoformat(),
                            "duration_seconds": self._elapsed_active_seconds(now),
                        }
                    )

        return result

    def get_incomplete_todo_count(self) -> int:
        return self.db.get_incomplete_todo_count()

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

    def complete_todo_task(self, todo_task_id: int) -> None:
        self.db.complete_todo_task(todo_task_id)

    def move_todo_task(self, todo_task_id: int, direction: int) -> None:
        self.db.move_todo_task(todo_task_id, direction)

    def set_todo_task_order(self, ordered_ids: list[int]) -> None:
        self.db.set_todo_task_order(ordered_ids)

    def sort_todo_tasks_by_deadline(self) -> None:
        self.db.sort_todo_tasks_by_deadline()

    # ── Backup ──────────────────────────────────────────────────────────
    def export_data(self) -> dict:
        return self.db.export_data()

    def import_data(self, data: dict) -> None:
        self.db.import_data(data)
