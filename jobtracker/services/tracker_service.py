"""
Business-logic layer between UI and database.

- Subjects: timed entries with sessions
- Tasks: completable todo items with optional deadlines
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

from ..core.database import db
from ..core.models import Subject, Session, TodoTask


class TrackerService:
    @staticmethod
    def _safe_parse_iso(value: str | None) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def __init__(self) -> None:
        # Recover from crashes that left one or more sessions open.
        open_sessions = db.get_open_sessions()
        self.active_session: Optional[Session] = open_sessions[0] if open_sessions else None
        self.active_subject: Optional[Subject] = None

        # Close stale parallel open sessions to keep a single active timer invariant.
        if len(open_sessions) > 1:
            now = datetime.now()
            for stale in open_sessions[1:]:
                if stale.id is None:
                    continue
                started_at = self._safe_parse_iso(stale.start_time)
                duration = max(0, int((now - started_at).total_seconds())) if started_at else 0
                db.stop_session(stale.id, now, duration)

        if self.active_session:
            self.active_subject = db.get_subject(self.active_session.subject_id)
            # If the subject was deleted while a session was open, close it.
            if self.active_subject is None:
                if self.active_session.id is not None:
                    db.stop_session(self.active_session.id, datetime.now(), 0)
                self.active_session = None

    def _set_active_subject(self, subject: Optional[Subject]) -> None:
        self.active_subject = subject

    # ── Subjects (timed) ────────────────────────────────────────────────
    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        return db.get_all_subjects(archived=archived)

    def get_all_subjects_including_archived(self) -> List[Subject]:
        return db.get_all_subjects_including_archived()

    def add_subject(self, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        if db.get_subject_by_name(normalized_name):
            return None
        return db.add_subject(normalized_name, normalized_color, normalized_notes)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        existing = db.get_subject_by_name(normalized_name)
        if existing and existing.id != subject_id:
            return None
        return db.update_subject(subject_id, normalized_name, normalized_color, normalized_notes)

    def delete_subject(self, subject_id: int) -> None:
        # Stop the timer before deleting the subject row.
        if self.active_subject and self.active_subject.id == subject_id:
            self.stop_active_subject()
        db.delete_subject(subject_id)  # CASCADE deletes sessions too

    def move_subject(self, subject_id: int, direction: int) -> None:
        db.move_subject(subject_id, direction)

    def set_subject_order(self, ordered_ids: list[int]) -> None:
        db.set_subject_order(ordered_ids)

    def archive_subject(self, subject_id: int) -> None:
        if self.active_subject and self.active_subject.id == subject_id:
            self.stop_active_subject()
        db.archive_subject(subject_id)

    def unarchive_subject(self, subject_id: int) -> None:
        db.unarchive_subject(subject_id)

    # ── Timer (subjects) ────────────────────────────────────────────────
    def start_subject(self, subject_id: int) -> bool:
        if self.active_session:
            return False
        try:
            self.active_session = db.start_session(subject_id)
        except ValueError:
            return False
        if self.active_session.id is None:
            self.active_session = None
            return False
        self._set_active_subject(db.get_subject(subject_id))
        if self.active_subject is None:
            db.stop_session(self.active_session.id, datetime.now(), 0)
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
        start_dt = self._safe_parse_iso(self.active_session.start_time)
        duration = max(0, int((end_dt - start_dt).total_seconds())) if start_dt else 0
        db.stop_session(self.active_session.id, end_dt, duration)
        self.active_session = None
        self._set_active_subject(None)

    # ── Stats (subjects) ────────────────────────────────────────────────
    def get_subject_stats(self, subject_id: int, filter_type: str = "Total") -> int:
        since_iso = None
        now = datetime.now()
        if filter_type == "Today":
            start_of_day = datetime.combine(date.today(), time(hour=3))
            if now < start_of_day:
                start_of_day -= timedelta(days=1)
            since_iso = start_of_day.isoformat()
        elif filter_type == "Last 7 days":
            since_iso = (now - timedelta(days=7)).isoformat()
        elif filter_type == "Last 30 days":
            since_iso = (now - timedelta(days=30)).isoformat()
        return db.get_subject_stats(subject_id, since_iso)

    def get_daily_subject_breakdown(self, days: int | None = 10) -> list[dict]:
        """Return the last N days of stacked work data in chronological session order.

        If *days* is ``None`` every day from the earliest recorded session to
        today is included ("All Time" mode).
        """
        today = date.today()

        if days is None:
            earliest_iso = db.get_earliest_session_date()
            if earliest_iso:
                try:
                    earliest = datetime.fromisoformat(earliest_iso).date()
                except ValueError:
                    earliest = today
            else:
                earliest = today
            start_day = earliest
            num_days = max(1, (today - start_day).days + 1)
        else:
            num_days = max(1, days)
            start_day = today - timedelta(days=num_days - 1)

        day_keys = [
            (start_day + timedelta(days=i)).isoformat()
            for i in range(num_days)
        ]

        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}

        # Collect sessions ordered chronologically per day.
        buckets: dict[str, list[dict]] = {day: [] for day in day_keys}

        sessions = db.get_closed_sessions_since(f"{start_day.isoformat()}T00:00:00")
        for sess in sessions:
            day = sess.start_time[:10]
            if day not in buckets:
                continue
            subject = subjects.get(sess.subject_id)
            if not subject or sess.duration_seconds <= 0:
                continue
            buckets[day].append(
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
            day = self.active_session.start_time[:10]
            if day in buckets:
                started_at = self._safe_parse_iso(self.active_session.start_time)
                subject = subjects.get(self.active_session.subject_id)
                if started_at and subject:
                    live_seconds = max(0, int((datetime.now() - started_at).total_seconds()))
                    if live_seconds > 0:
                        buckets[day].append(
                            {
                                "subject_id": self.active_session.subject_id,
                                "subject_name": subject.name,
                                "color": subject.color,
                                "seconds": live_seconds,
                                "start_time": self.active_session.start_time,
                                "end_time": datetime.now().isoformat(),
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
        """Return all closed sessions between two dates with subject metadata.

        Used by the agenda timeline view.
        """
        subjects = {s.id: s for s in self.get_all_subjects_including_archived() if s.id is not None}
        since_iso = f"{since_date.isoformat()}T00:00:00"
        until_iso = f"{(until_date + timedelta(days=1)).isoformat()}T00:00:00"
        sessions = db.get_all_closed_sessions_in_range(since_iso, until_iso)

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
            started_at = self._safe_parse_iso(self.active_session.start_time)
            subject = subjects.get(self.active_session.subject_id)
            if started_at and subject:
                sess_date = started_at.date()
                if since_date <= sess_date <= until_date:
                    live_seconds = max(0, int((datetime.now() - started_at).total_seconds()))
                    result.append(
                        {
                            "session_id": None,
                            "subject_id": self.active_session.subject_id,
                            "subject_name": subject.name,
                            "color": subject.color,
                            "start_time": self.active_session.start_time,
                            "end_time": datetime.now().isoformat(),
                            "duration_seconds": live_seconds,
                        }
                    )

        return result

    def get_incomplete_todo_count(self) -> int:
        return db.get_incomplete_todo_count()

    # ── Sessions (subjects) ─────────────────────────────────────────────
    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        return db.get_sessions_for_subject(subject_id)

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        note: Optional[str] = None,
    ) -> Session:
        duration = max(0, int((end_time - start_time).total_seconds()))
        return db.add_session(subject_id, start_time, end_time, duration, note)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        note: Optional[str] = None,
    ) -> Optional[Session]:
        duration = max(0, int((end_time - start_time).total_seconds()))
        return db.update_session(session_id, subject_id, start_time, end_time, duration, note)

    def delete_session(self, session_id: int) -> None:
        db.delete_session(session_id)

    # ── Todo Tasks (completable) ────────────────────────────────────────
    def get_all_todo_tasks(self) -> List[TodoTask]:
        return db.get_all_todo_tasks()

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return db.add_todo_task(normalized_name, normalized_notes, normalized_deadline)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_deadline = deadline or None
        if not normalized_name:
            return None
        return db.update_todo_task(todo_task_id, normalized_name, normalized_notes, normalized_deadline)

    def delete_todo_task(self, todo_task_id: int) -> None:
        db.delete_todo_task(todo_task_id)

    def move_todo_task(self, todo_task_id: int, direction: int) -> None:
        db.move_todo_task(todo_task_id, direction)

    def set_todo_task_order(self, ordered_ids: list[int]) -> None:
        db.set_todo_task_order(ordered_ids)

    def sort_todo_tasks_by_deadline(self) -> None:
        db.sort_todo_tasks_by_deadline()

    # ── Backup ──────────────────────────────────────────────────────────
    def export_data(self) -> dict:
        return db.export_data()

    def import_data(self, data: dict) -> None:
        db.import_data(data)
