"""Closed-session editing, duplication and deletion use cases."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from ..core.models import Session
from ..core import timeutils


class SessionService:
    """Session edits call facade mutations so synced writes stay queued."""

    def __init__(self, tracker):
        self._tracker = tracker

    def get_subject_deletion_summary(self, subject_id: int) -> dict:
        """Facts needed to warn before deleting a subject with history."""
        sessions = self._tracker.db.get_sessions_for_subject(subject_id)
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

    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        return self._tracker.db.get_sessions_for_subject(subject_id)

    def get_session(self, session_id: int) -> Optional[Session]:
        return self._tracker.db.get_session(session_id)

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Session]:
        if (
            not timeutils.is_local_datetime(start_time)
            or not timeutils.is_local_datetime(end_time)
            or end_time < start_time
            or self._tracker.db.get_subject(subject_id) is None
        ):
            return None
        duration = timeutils.duration_seconds(start_time, end_time)
        return self._tracker.db.add_session(subject_id, start_time, end_time, duration)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Session]:
        session = self._tracker.db.get_session(session_id)
        if (
            session is None or session.end_time is None
            or not timeutils.is_local_datetime(start_time)
            or not timeutils.is_local_datetime(end_time)
            or end_time < start_time
            or self._tracker.db.get_subject(subject_id) is None
        ):
            return None
        duration = timeutils.duration_seconds(start_time, end_time)
        return self._tracker.db.update_session(session_id, subject_id, start_time, end_time, duration)

    def shift_session(self, session_id: int, seconds: int) -> Optional[Session]:
        """Move a closed session in time by ``seconds`` (negative = earlier).

        Start and end move together, so the duration and the subject are
        untouched. Used by the ±15m / ±1h nudge buttons.
        """
        session = self._tracker.db.get_session(session_id)
        if session is None or session.id is None or not session.end_time:
            return None
        start_dt = timeutils.parse_iso(session.start_time)
        end_dt = timeutils.parse_iso(session.end_time)
        if start_dt is None or end_dt is None:
            return None
        delta = timedelta(seconds=int(seconds))
        return self._tracker.update_session(
            session.id, session.subject_id, start_dt + delta, end_dt + delta
        )

    def delete_session(self, session_id: int) -> None:
        self._tracker.db.delete_session(session_id)
        if self._tracker.active_session and self._tracker.active_session.id == session_id:
            self._tracker._adopt_open_session()

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
        session = self._tracker.db.get_session(session_id)
        if session is None or session.id is None or not session.end_time:
            return None
        start_dt = timeutils.parse_iso(session.start_time)
        end_dt = timeutils.parse_iso(session.end_time)
        if start_dt is None or end_dt is None:
            return None

        day_start = self._tracker.get_day_start()
        now = datetime.now()
        if to == "next_day":
            target_day = timeutils.logical_day(start_dt, day_start) + timedelta(days=1)
        elif to == "today":
            target_day = timeutils.logical_day(now, day_start)
        else:
            return None

        new_start, new_end = timeutils.shift_session_to_logical_day(
            start_dt, end_dt, target_day, day_start
        )
        return self._tracker.add_session(session.subject_id, new_start, new_end)
