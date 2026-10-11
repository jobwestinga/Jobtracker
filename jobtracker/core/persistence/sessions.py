"""Session writes, heartbeat, recovery and aggregate queries."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from ..models import Session


class SessionRepository:
    """Session writes, heartbeat, recovery and aggregate queries."""

    def __init__(self, database):
        self._database = database

    def start_session(
        self, subject_id: int, device_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
    ) -> Session:
        if self._database.get_subject(subject_id) is None:
            raise ValueError(f"Cannot start session for missing subject id {subject_id}")
        cur = self._database.connection.cursor()
        start_time = (start_time or datetime.now()).isoformat()
        # Seed last_active_at with the start time so a session is always
        # "known active" from the moment it begins.
        cur.execute(
            "INSERT INTO sessions (task_id, start_time, last_active_at, device_id) "
            "VALUES (?, ?, ?, ?)",
            (subject_id, start_time, start_time, device_id),
        )
        self._database.connection.commit()
        return self._database.get_session(cur.lastrowid)

    def touch_active_session(self, session_id: int, when_iso: Optional[str] = None) -> None:
        """Update the 'last known active' timestamp for an open session.

        No-op if the session is already closed (``end_time`` set). This is the
        heartbeat write — one cheap UPDATE, no new rows, no history.
        """
        when_iso = when_iso or datetime.now().isoformat()
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ? AND end_time IS NULL",
            (when_iso, session_id),
        )
        self._database.connection.commit()

    def stop_session(
        self, session_id: int, end_time: datetime, duration_seconds: int
    ) -> None:
        cur = self._database.connection.cursor()
        if duration_seconds < 30:
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        else:
            cur.execute(
                "UPDATE sessions SET end_time = ?, duration_seconds = ? WHERE id = ?",
                (end_time.isoformat(), duration_seconds, session_id),
            )
        self._database.connection.commit()

    def close_recovered_session(
        self,
        session_id: int,
        end_time: datetime,
        duration_seconds: int,
    ) -> None:
        """Close an unfinished session without applying the 30-second deletion.

        This is only for automatic startup reconciliation of unexpected parallel
        open rows. Recovery must never silently delete unfinished user data.
        Normal user-initiated stop/add/edit operations still enforce the existing
        30-second minimum through :meth:`stop_session`.
        """
        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE sessions SET end_time = ?, duration_seconds = ? "
            "WHERE id = ? AND end_time IS NULL",
            (end_time.isoformat(), max(0, int(duration_seconds)), session_id),
        )
        self._database.connection.commit()

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
    ) -> Optional[Session]:
        if duration_seconds < 30:
            return None
        cur = self._database.connection.cursor()
        cur.execute(
            "INSERT INTO sessions (task_id, start_time, end_time, duration_seconds) "
            "VALUES (?, ?, ?, ?)",
            (subject_id, start_time.isoformat(), end_time.isoformat(), duration_seconds),
        )
        self._database.connection.commit()
        return self._database.get_session(cur.lastrowid)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
    ) -> Optional[Session]:
        cur = self._database.connection.cursor()
        if duration_seconds < 30:
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._database.connection.commit()
            return None
        cur.execute(
            "UPDATE sessions SET task_id = ?, start_time = ?, end_time = ?, "
            "duration_seconds = ? WHERE id = ?",
            (subject_id, start_time.isoformat(), end_time.isoformat(), duration_seconds, session_id),
        )
        self._database.connection.commit()
        return self._database.get_session(session_id)

    def delete_session(self, session_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._database.connection.commit()

    def get_session(self, session_id: int) -> Optional[Session]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        return Session(**dict(row)) if row else None

    def get_open_sessions(self) -> List[Session]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM sessions WHERE end_time IS NULL ORDER BY start_time DESC")
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_open_session(self) -> Optional[Session]:
        open_sessions = self._database.get_open_sessions()
        return open_sessions[0] if open_sessions else None

    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT * FROM sessions WHERE task_id = ? ORDER BY start_time DESC",
            (subject_id,),
        )
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_all_closed_sessions_in_range(
        self, since_iso: str, until_iso: str
    ) -> List[Session]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT * FROM sessions WHERE end_time IS NOT NULL "
            "AND start_time >= ? AND start_time < ? ORDER BY start_time ASC",
            (since_iso, until_iso),
        )
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_earliest_session_date(self) -> Optional[str]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT MIN(start_time) AS earliest FROM sessions WHERE end_time IS NOT NULL"
        )
        row = cur.fetchone()
        return row["earliest"] if row and row["earliest"] else None

    def get_latest_session_date(self) -> Optional[str]:
        """Latest session START, including sessions scheduled ahead of now.

        Graph windows use this so a session created for a future day is
        actually visible instead of falling outside every range.
        """
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT MAX(start_time) AS latest FROM sessions WHERE end_time IS NOT NULL"
        )
        row = cur.fetchone()
        return row["latest"] if row and row["latest"] else None

    def get_subject_stats(self, subject_id: int, since_iso: Optional[str] = None) -> int:
        cur = self._database.connection.cursor()
        if since_iso:
            cur.execute(
                "SELECT SUM(duration_seconds) AS total FROM sessions "
                "WHERE task_id = ? AND start_time >= ? AND end_time IS NOT NULL",
                (subject_id, since_iso),
            )
        else:
            cur.execute(
                "SELECT SUM(duration_seconds) AS total FROM sessions "
                "WHERE task_id = ? AND end_time IS NOT NULL",
                (subject_id,),
            )
        row = cur.fetchone()
        return row["total"] if row and row["total"] else 0

    def get_all_subject_stats(self, since_iso: Optional[str] = None) -> dict[int, int]:
        """Closed-session totals for every subject in one query.

        Replaces one aggregate query per subject card on every list reload.
        """
        cur = self._database.connection.cursor()
        if since_iso:
            cur.execute(
                "SELECT task_id, SUM(duration_seconds) AS total FROM sessions "
                "WHERE end_time IS NOT NULL AND start_time >= ? GROUP BY task_id",
                (since_iso,),
            )
        else:
            cur.execute(
                "SELECT task_id, SUM(duration_seconds) AS total FROM sessions "
                "WHERE end_time IS NOT NULL GROUP BY task_id"
            )
        return {int(row["task_id"]): int(row["total"] or 0) for row in cur.fetchall()}
