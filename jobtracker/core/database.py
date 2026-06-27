"""
SQLite persistence layer for JobTracker.

Tables:
  tasks      — timed subjects (name, color, notes, manual sort order)
  sessions   — task_id -> tasks.id, start/end times, duration, optional note
  todo_tasks — goals (legacy table name retained), completion and manual order
  milestones — ordered checklist items belonging to goals
  goal_templates — daily/weekly/monthly generators for normal goal instances
  settings   — key/value store for user preferences
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import List, Optional, Union
from pathlib import Path

from .config import DB_PATH
from .models import Subject, Session, TodoTask, Milestone, GoalTemplate

logger = logging.getLogger("jobtracker")


class Database:
    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        # ``db_path`` lets tests / tooling use an isolated database. Production
        # code uses the module-level ``db`` singleton, which defaults to the
        # configured DB_PATH (see config.py, also overridable via env var).
        path = str(db_path) if db_path is not None else str(DB_PATH)
        self.db_path = path
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._init_db()

    # ── Schema ───────────────────────────────────────────────────────────
    def _init_db(self) -> None:
        cur = self.connection.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                notes TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                duration_seconds INTEGER DEFAULT 0,
                note TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS todo_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                notes TEXT DEFAULT '',
                deadline TIMESTAMP,
                is_completed INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        # Goal milestones (checklist items belonging to a todo_tasks/Goal row).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                note TEXT DEFAULT '',
                is_done INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (goal_id) REFERENCES todo_tasks (id) ON DELETE CASCADE
            )
            """
        )

        # Recurring templates that append normal Goal instances once per logical
        # period. Milestones to copy are stored as JSON.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                notes TEXT DEFAULT '',
                recurrence TEXT NOT NULL DEFAULT 'daily',
                milestones_json TEXT DEFAULT '[]',
                last_generated TEXT,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Migration for sessions.note
        if not self._column_exists("sessions", "note"):
            cur.execute("ALTER TABLE sessions ADD COLUMN note TEXT")

        # Migration for tasks.sort_order
        if not self._column_exists("tasks", "sort_order"):
            cur.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER DEFAULT 0")

        # Migration for tasks.is_archived
        if not self._column_exists("tasks", "is_archived"):
            cur.execute("ALTER TABLE tasks ADD COLUMN is_archived INTEGER DEFAULT 0")

        # Migration for sessions.last_active_at — a single nullable timestamp per
        # session, heartbeated ~once/minute while the session is active. This is
        # additive and reversible (the column can be ignored or dropped without
        # affecting any existing read path; historical rows simply hold NULL).
        if not self._column_exists("sessions", "last_active_at"):
            cur.execute("ALTER TABLE sessions ADD COLUMN last_active_at TIMESTAMP")

        # Migration for todo_tasks.template_id — which recurring template (if any)
        # produced this Goal instance. Additive, nullable, reversible.
        if not self._column_exists("todo_tasks", "template_id"):
            cur.execute("ALTER TABLE todo_tasks ADD COLUMN template_id INTEGER")

        # Main analytics/stat queries filter by start time and subject. These
        # additive indexes keep graph switching responsive as history grows.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_start_time "
            "ON sessions(start_time)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_task_start "
            "ON sessions(task_id, start_time)"
        )

        # Ensure task rows have a deterministic manual order.
        cur.execute(
            "UPDATE tasks SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"
        )

        # Ensure todo rows have a deterministic manual order.
        cur.execute(
            "UPDATE todo_tasks SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"
        )

        self.connection.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        cur = self.connection.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return any(row["name"] == column for row in cur.fetchall())

    def _next_sort_order(self, table: str) -> int:
        cur = self.connection.cursor()
        cur.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM {table}")
        return int(cur.fetchone()["next_order"])

    def _set_order(self, table: str, id_column: str, ordered_ids: list[int]) -> None:
        normalized_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in ordered_ids:
            try:
                row_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if row_id in seen:
                continue
            seen.add(row_id)
            normalized_ids.append(row_id)

        if not normalized_ids:
            return

        cur = self.connection.cursor()
        cur.execute(f"SELECT {id_column} FROM {table} ORDER BY sort_order ASC, {id_column} ASC")
        existing_ids = [int(row[id_column]) for row in cur.fetchall()]
        if not existing_ids:
            return

        ordered_existing = [row_id for row_id in normalized_ids if row_id in existing_ids]
        missing_existing = [row_id for row_id in existing_ids if row_id not in ordered_existing]
        final_ids = ordered_existing + missing_existing

        for idx, row_id in enumerate(final_ids, start=1):
            cur.execute(
                f"UPDATE {table} SET sort_order = ? WHERE {id_column} = ?",
                (idx, row_id),
            )
        self.connection.commit()

    # ── Settings ─────────────────────────────────────────────────────────
    def get_setting(self, key: str, default: str = "") -> str:
        cur = self.connection.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.connection.commit()

    # ── Subjects (timed) ─────────────────────────────────────────────────
    def add_subject(self, name: str, color: str, notes: str) -> Subject:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Subject name cannot be empty")

        cur = self.connection.cursor()
        cur.execute(
            "INSERT INTO tasks (name, color, notes, sort_order) VALUES (?, ?, ?, ?)",
            (
                normalized_name,
                (color or "").strip() or "#3B82F6",
                (notes or "").strip(),
                self._next_sort_order("tasks"),
            ),
        )
        self.connection.commit()
        return self.get_subject(cur.lastrowid)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        cur = self.connection.cursor()
        cur.execute(
            "UPDATE tasks SET name = ?, color = ?, notes = ? WHERE id = ?",
            (normalized_name, (color or "").strip() or "#3B82F6", (notes or "").strip(), subject_id),
        )
        self.connection.commit()
        return self.get_subject(subject_id)

    def delete_subject(self, subject_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("DELETE FROM tasks WHERE id = ?", (subject_id,))
        self.connection.commit()

    def get_subject(self, subject_id: int) -> Optional[Subject]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (subject_id,))
        row = cur.fetchone()
        return Subject(**dict(row)) if row else None

    def get_subject_by_name(self, name: str) -> Optional[Subject]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM tasks WHERE name = ? COLLATE NOCASE", (name,))
        row = cur.fetchone()
        return Subject(**dict(row)) if row else None

    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE is_archived = ? ORDER BY sort_order ASC, created_at DESC",
            (1 if archived else 0,),
        )
        return [Subject(**dict(row)) for row in cur.fetchall()]

    def get_all_subjects_including_archived(self) -> List[Subject]:
        """Return every subject regardless of archive status (for graphs)."""
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY sort_order ASC, created_at DESC")
        return [Subject(**dict(row)) for row in cur.fetchall()]

    def archive_subject(self, subject_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("UPDATE tasks SET is_archived = 1 WHERE id = ?", (subject_id,))
        self.connection.commit()

    def unarchive_subject(self, subject_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("UPDATE tasks SET is_archived = 0 WHERE id = ?", (subject_id,))
        self.connection.commit()

    def move_subject(self, subject_id: int, direction: int) -> None:
        subjects = self.get_all_subjects()
        ids = [s.id for s in subjects if s.id is not None]
        if subject_id not in ids:
            return
        idx = ids.index(subject_id)
        target = idx + direction
        if target < 0 or target >= len(ids):
            return
        ids[idx], ids[target] = ids[target], ids[idx]
        self._set_order("tasks", "id", ids)

    def set_subject_order(self, ordered_ids: list[int]) -> None:
        if not ordered_ids:
            return
        self._set_order("tasks", "id", ordered_ids)

    # ── Sessions ─────────────────────────────────────────────────────────
    def start_session(self, subject_id: int) -> Session:
        if self.get_subject(subject_id) is None:
            raise ValueError(f"Cannot start session for missing subject id {subject_id}")
        cur = self.connection.cursor()
        start_time = datetime.now().isoformat()
        # Seed last_active_at with the start time so a session is always
        # "known active" from the moment it begins.
        cur.execute(
            "INSERT INTO sessions (task_id, start_time, last_active_at) VALUES (?, ?, ?)",
            (subject_id, start_time, start_time),
        )
        self.connection.commit()
        return self.get_session(cur.lastrowid)

    def touch_active_session(self, session_id: int, when_iso: Optional[str] = None) -> None:
        """Update the 'last known active' timestamp for an open session.

        No-op if the session is already closed (``end_time`` set). This is the
        heartbeat write — one cheap UPDATE, no new rows, no history.
        """
        when_iso = when_iso or datetime.now().isoformat()
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ? AND end_time IS NULL",
            (when_iso, session_id),
        )
        self.connection.commit()

    def stop_session(
        self, session_id: int, end_time: datetime, duration_seconds: int, note: Optional[str] = None
    ) -> None:
        cur = self.connection.cursor()
        if duration_seconds < 30:
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        else:
            cur.execute(
                "UPDATE sessions SET end_time = ?, duration_seconds = ?, note = ? WHERE id = ?",
                (end_time.isoformat(), duration_seconds, note, session_id),
            )
        self.connection.commit()

    def close_recovered_session(
        self,
        session_id: int,
        end_time: datetime,
        duration_seconds: int,
        note: Optional[str] = None,
    ) -> None:
        """Close an unfinished session without applying the 30-second deletion.

        This is only for automatic startup reconciliation of unexpected parallel
        open rows. Recovery must never silently delete unfinished user data.
        Normal user-initiated stop/add/edit operations still enforce the existing
        30-second minimum through :meth:`stop_session`.
        """
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE sessions SET end_time = ?, duration_seconds = ?, note = ? "
            "WHERE id = ? AND end_time IS NULL",
            (end_time.isoformat(), max(0, int(duration_seconds)), note, session_id),
        )
        self.connection.commit()

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
        note: Optional[str] = None,
    ) -> Optional[Session]:
        if duration_seconds < 30:
            return None
        cur = self.connection.cursor()
        cur.execute(
            "INSERT INTO sessions (task_id, start_time, end_time, duration_seconds, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (subject_id, start_time.isoformat(), end_time.isoformat(), duration_seconds, note),
        )
        self.connection.commit()
        return self.get_session(cur.lastrowid)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
        note: Optional[str] = None,
    ) -> Optional[Session]:
        cur = self.connection.cursor()
        if duration_seconds < 30:
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.connection.commit()
            return None
        cur.execute(
            "UPDATE sessions SET task_id = ?, start_time = ?, end_time = ?, "
            "duration_seconds = ?, note = ? WHERE id = ?",
            (subject_id, start_time.isoformat(), end_time.isoformat(), duration_seconds, note, session_id),
        )
        self.connection.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.connection.commit()

    def get_session(self, session_id: int) -> Optional[Session]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        return Session(**dict(row)) if row else None

    def get_open_sessions(self) -> List[Session]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM sessions WHERE end_time IS NULL ORDER BY start_time DESC")
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_open_session(self) -> Optional[Session]:
        open_sessions = self.get_open_sessions()
        return open_sessions[0] if open_sessions else None

    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM sessions WHERE task_id = ? ORDER BY start_time DESC",
            (subject_id,),
        )
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_closed_sessions_since(self, since_iso: str) -> List[Session]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM sessions WHERE end_time IS NOT NULL AND start_time >= ? ORDER BY start_time ASC",
            (since_iso,),
        )
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_all_closed_sessions_in_range(
        self, since_iso: str, until_iso: str
    ) -> List[Session]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM sessions WHERE end_time IS NOT NULL "
            "AND start_time >= ? AND start_time < ? ORDER BY start_time ASC",
            (since_iso, until_iso),
        )
        return [Session(**dict(row)) for row in cur.fetchall()]

    def get_earliest_session_date(self) -> Optional[str]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT MIN(start_time) AS earliest FROM sessions WHERE end_time IS NOT NULL"
        )
        row = cur.fetchone()
        return row["earliest"] if row and row["earliest"] else None

    def get_subject_stats(self, subject_id: int, since_iso: Optional[str] = None) -> int:
        cur = self.connection.cursor()
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

    def get_incomplete_todo_count(self) -> int:
        cur = self.connection.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM todo_tasks WHERE is_completed = 0")
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0

    # ── Todo Tasks (completable) ─────────────────────────────────────────
    def _todo_order_mode(self) -> str:
        mode = self.get_setting("todo_order_mode", "deadline")
        if mode in {"deadline", "manual"}:
            return mode
        return "deadline"

    def _todo_ids_deadline_order(self) -> list[int]:
        cur = self.connection.cursor()
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

    def _todo_ids_manual_order(self) -> list[int]:
        cur = self.connection.cursor()
        cur.execute("SELECT id FROM todo_tasks ORDER BY sort_order ASC, created_at ASC")
        return [int(row["id"]) for row in cur.fetchall()]

    def _ensure_manual_todo_order(self) -> None:
        if self._todo_order_mode() == "manual":
            return
        ordered_ids = self._todo_ids_deadline_order()
        self._set_order("todo_tasks", "id", ordered_ids)
        self.set_setting("todo_order_mode", "manual")

    def ensure_manual_goal_order(self) -> None:
        """Migrate the legacy deadline-sorted Tasks view to manual Goal order.

        The currently visible deadline order is materialised first, so the
        redesign does not unexpectedly scramble existing items.
        """
        self._ensure_manual_todo_order()

    def sort_todo_tasks_by_deadline(self) -> None:
        ordered_ids = self._todo_ids_deadline_order()
        self._set_order("todo_tasks", "id", ordered_ids)
        self.set_setting("todo_order_mode", "deadline")

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> TodoTask:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Todo task name cannot be empty")

        cur = self.connection.cursor()
        cur.execute(
            "INSERT INTO todo_tasks (name, notes, deadline, sort_order) VALUES (?, ?, ?, ?)",
            (normalized_name, (notes or "").strip(), deadline or None, self._next_sort_order("todo_tasks")),
        )
        self.connection.commit()
        todo_task_id = cur.lastrowid
        if self._todo_order_mode() == "deadline":
            self.sort_todo_tasks_by_deadline()
        return self.get_todo_task(todo_task_id)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        cur = self.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET name = ?, notes = ?, deadline = ? WHERE id = ?",
            (normalized_name, (notes or "").strip(), deadline or None, todo_task_id),
        )
        self.connection.commit()
        if self._todo_order_mode() == "deadline":
            self.sort_todo_tasks_by_deadline()
        return self.get_todo_task(todo_task_id)

    def delete_todo_task(self, todo_task_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("DELETE FROM todo_tasks WHERE id = ?", (todo_task_id,))
        self.connection.commit()

    def get_todo_task(self, todo_task_id: int) -> Optional[TodoTask]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM todo_tasks WHERE id = ?", (todo_task_id,))
        row = cur.fetchone()
        return TodoTask(**dict(row)) if row else None

    def get_all_todo_tasks(self) -> List[TodoTask]:
        cur = self.connection.cursor()
        if self._todo_order_mode() == "manual":
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
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM todo_tasks WHERE is_completed = 1 "
            "ORDER BY created_at DESC, id DESC"
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
        cur = self.connection.cursor()
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
        self.connection.commit()
        self.set_setting("todo_order_mode", "manual")
        return self.get_todo_task(cur.lastrowid)

    def complete_todo_task(self, todo_task_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_completed = 1 WHERE id = ?", (todo_task_id,)
        )
        self.connection.commit()

    def uncomplete_todo_task(self, todo_task_id: int) -> None:
        """Reopen a completed Goal. Completion is always reversible."""
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_completed = 0 WHERE id = ?", (todo_task_id,)
        )
        self.connection.commit()

    def move_todo_task(self, todo_task_id: int, direction: int) -> None:
        self._ensure_manual_todo_order()
        ids = self._todo_ids_manual_order()
        if todo_task_id not in ids:
            return
        idx = ids.index(todo_task_id)
        target = idx + direction
        if target < 0 or target >= len(ids):
            return
        ids[idx], ids[target] = ids[target], ids[idx]
        self._set_order("todo_tasks", "id", ids)

    def set_todo_task_order(self, ordered_ids: list[int]) -> None:
        if not ordered_ids:
            return
        self.set_setting("todo_order_mode", "manual")
        self._set_order("todo_tasks", "id", ordered_ids)

    # ── Milestones ───────────────────────────────────────────────────────
    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        cur = self.connection.cursor()
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS nxt FROM milestones WHERE goal_id = ?", (goal_id,))
        order = int(cur.fetchone()["nxt"])
        cur.execute(
            "INSERT INTO milestones (goal_id, title, note, sort_order) VALUES (?, ?, ?, ?)",
            (goal_id, normalized, (note or "").strip(), order),
        )
        self.connection.commit()
        return self.get_milestone(cur.lastrowid)

    def get_milestone(self, milestone_id: int) -> Optional[Milestone]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
        row = cur.fetchone()
        return Milestone(**dict(row)) if row else None

    def get_milestones(self, goal_id: int) -> List[Milestone]:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT * FROM milestones WHERE goal_id = ? ORDER BY sort_order ASC, id ASC",
            (goal_id,),
        )
        return [Milestone(**dict(row)) for row in cur.fetchall()]

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE milestones SET title = ?, note = ? WHERE id = ?",
            (normalized, (note or "").strip(), milestone_id),
        )
        self.connection.commit()
        return self.get_milestone(milestone_id)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE milestones SET is_done = ? WHERE id = ?",
            (1 if done else 0, milestone_id),
        )
        self.connection.commit()

    def delete_milestone(self, milestone_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
        self.connection.commit()

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        for idx, mid in enumerate(ordered_ids, start=1):
            self.connection.execute(
                "UPDATE milestones SET sort_order = ? WHERE id = ? AND goal_id = ?",
                (idx, int(mid), goal_id),
            )
        self.connection.commit()

    # ── Goal templates (recurring) ───────────────────────────────────────
    def add_goal_template(
        self, title: str, notes: str, recurrence: str, milestones_json: str = "[]"
    ) -> Optional[GoalTemplate]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        if recurrence not in ("daily", "weekly", "monthly"):
            recurrence = "daily"
        cur = self.connection.cursor()
        cur.execute(
            "INSERT INTO goal_templates (title, notes, recurrence, milestones_json, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (normalized, (notes or "").strip(), recurrence, milestones_json or "[]",
             self._next_sort_order("goal_templates")),
        )
        self.connection.commit()
        return self.get_goal_template(cur.lastrowid)

    def get_goal_template(self, template_id: int) -> Optional[GoalTemplate]:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM goal_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        return GoalTemplate(**dict(row)) if row else None

    def get_goal_templates(self, active_only: bool = False) -> List[GoalTemplate]:
        cur = self.connection.cursor()
        if active_only:
            cur.execute("SELECT * FROM goal_templates WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
        else:
            cur.execute("SELECT * FROM goal_templates ORDER BY sort_order ASC, id ASC")
        return [GoalTemplate(**dict(row)) for row in cur.fetchall()]

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str, milestones_json: str
    ) -> Optional[GoalTemplate]:
        normalized = (title or "").strip()
        if not normalized:
            return None
        if recurrence not in ("daily", "weekly", "monthly"):
            recurrence = "daily"
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET title = ?, notes = ?, recurrence = ?, milestones_json = ? WHERE id = ?",
            (normalized, (notes or "").strip(), recurrence, milestones_json or "[]", template_id),
        )
        self.connection.commit()
        return self.get_goal_template(template_id)

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET is_active = ? WHERE id = ?",
            (1 if active else 0, template_id),
        )
        self.connection.commit()

    def delete_goal_template(self, template_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute("DELETE FROM goal_templates WHERE id = ?", (template_id,))
        self.connection.commit()

    def set_goal_template_last_generated(self, template_id: int, period_key: str) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE goal_templates SET last_generated = ? WHERE id = ?",
            (period_key, template_id),
        )
        self.connection.commit()

    # ── Backup / Restore ─────────────────────────────────────────────────
    def export_data(self) -> dict:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM tasks")
        subjects = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM sessions")
        sessions = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM todo_tasks")
        todo_tasks = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM milestones")
        milestones = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM goal_templates")
        goal_templates = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM settings")
        settings = [dict(row) for row in cur.fetchall()]

        logger.info(
            "Exported %d subjects, %d sessions, %d goals, %d milestones, "
            "%d templates, %d settings",
            len(subjects), len(sessions), len(todo_tasks), len(milestones),
            len(goal_templates), len(settings),
        )
        return {
            "subjects": subjects,
            "sessions": sessions,
            "todo_tasks": todo_tasks,
            "milestones": milestones,
            "goal_templates": goal_templates,
            "settings": settings,
        }

    def import_data(self, data: dict) -> None:
        """Atomically merge an authoritative JSON backup.

        A malformed row must not leave a half-imported database that gets
        committed by a later unrelated action. A savepoint works both when the
        connection is idle and when a caller already has a transaction open.
        """
        cursor = self.connection.cursor()
        cursor.execute("SAVEPOINT jobtracker_import")
        try:
            self._import_data_rows(data)
            cursor.execute("RELEASE SAVEPOINT jobtracker_import")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT jobtracker_import")
            cursor.execute("RELEASE SAVEPOINT jobtracker_import")
            logger.exception("Backup import rolled back")
            raise

    def _import_data_rows(self, data: dict) -> None:
        cur = self.connection.cursor()

        # Import subjects (legacy key: tasks)
        subjects = data.get("subjects", data.get("tasks", []))
        subject_id_map: dict[int, int] = {}

        for subject in subjects:
            if not subject.get("name"):
                continue
            legacy_id = subject.get("id")
            cur.execute("SELECT id FROM tasks WHERE name = ? COLLATE NOCASE", (subject["name"],))
            existing = cur.fetchone()
            if existing:
                if legacy_id is not None:
                    try:
                        subject_id_map[int(legacy_id)] = int(existing["id"])
                    except (TypeError, ValueError):
                        pass
                continue

            cur.execute(
                "INSERT INTO tasks "
                "(name, color, notes, sort_order, is_archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    subject["name"],
                    subject.get("color", "#3B82F6"),
                    subject.get("notes", ""),
                    subject.get("sort_order", self._next_sort_order("tasks")),
                    subject.get("is_archived", 0),
                    subject.get("created_at", datetime.now().isoformat()),
                ),
            )
            if legacy_id is not None:
                try:
                    subject_id_map[int(legacy_id)] = int(cur.lastrowid)
                except (TypeError, ValueError):
                    pass

        for sess in data.get("sessions", []):
            legacy_subject_id = sess.get("task_id", sess.get("subject_id"))
            if legacy_subject_id is None or not sess.get("start_time"):
                continue
            try:
                legacy_subject_id = int(legacy_subject_id)
            except (TypeError, ValueError):
                continue
            new_subject_id = subject_id_map.get(legacy_subject_id)
            if not new_subject_id:
                continue
            cur.execute(
                "SELECT id FROM sessions WHERE task_id = ? AND start_time = ? "
                "AND COALESCE(end_time, '') = COALESCE(?, '')",
                (new_subject_id, sess["start_time"], sess.get("end_time")),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO sessions "
                "(task_id, start_time, end_time, duration_seconds, note, last_active_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_subject_id,
                    sess["start_time"],
                    sess.get("end_time"),
                    sess.get("duration_seconds", 0),
                    sess.get("note"),
                    sess.get("last_active_at"),
                ),
            )

        # Import recurring templates first so generated goal instances can retain
        # their template association after IDs are remapped.
        template_id_map: dict[int, int] = {}
        for tpl in data.get("goal_templates", []):
            if not tpl.get("title"):
                continue
            legacy_template_id = tpl.get("id")
            recurrence = tpl.get("recurrence", "daily")
            cur.execute(
                "SELECT id FROM goal_templates "
                "WHERE title = ? COLLATE NOCASE AND recurrence = ?",
                (tpl["title"], recurrence),
            )
            existing = cur.fetchone()
            if existing:
                new_template_id = int(existing["id"])
            else:
                cur.execute(
                    "INSERT INTO goal_templates "
                    "(title, notes, recurrence, milestones_json, last_generated, "
                    "is_active, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tpl["title"],
                        tpl.get("notes", ""),
                        recurrence,
                        tpl.get("milestones_json", "[]"),
                        tpl.get("last_generated"),
                        tpl.get("is_active", 1),
                        tpl.get("sort_order", self._next_sort_order("goal_templates")),
                        tpl.get("created_at", datetime.now().isoformat()),
                    ),
                )
                new_template_id = int(cur.lastrowid)
            if legacy_template_id is not None:
                try:
                    template_id_map[int(legacy_template_id)] = new_template_id
                except (TypeError, ValueError):
                    pass

        # Import goals (legacy table name: todo_tasks); track ID mapping for
        # milestones. Created-at + sort-order are included in modern backup
        # identity so repeated generated goals with the same title are preserved.
        goal_id_map: dict[int, int] = {}
        for item in data.get("todo_tasks", []):
            if not item.get("name"):
                continue
            legacy_goal_id = item.get("id")
            created_at = item.get("created_at")
            sort_order = item.get("sort_order")
            if created_at is not None and sort_order is not None:
                cur.execute(
                    "SELECT id FROM todo_tasks WHERE name = ? COLLATE NOCASE "
                    "AND COALESCE(deadline, '') = COALESCE(?, '') "
                    "AND created_at = ? AND sort_order = ?",
                    (item["name"], item.get("deadline"), created_at, sort_order),
                )
            else:
                # Legacy payloads lacked reliable instance identity.
                cur.execute(
                    "SELECT id FROM todo_tasks WHERE name = ? COLLATE NOCASE "
                    "AND COALESCE(deadline, '') = COALESCE(?, '')",
                    (item["name"], item.get("deadline")),
                )
            existing = cur.fetchone()
            if existing:
                if legacy_goal_id is not None:
                    try:
                        goal_id_map[int(legacy_goal_id)] = int(existing["id"])
                    except (TypeError, ValueError):
                        pass
                continue
            legacy_template_id = item.get("template_id")
            try:
                new_template_id = template_id_map.get(int(legacy_template_id))
            except (TypeError, ValueError):
                new_template_id = None
            cur.execute(
                "INSERT INTO todo_tasks "
                "(name, notes, deadline, is_completed, sort_order, created_at, template_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item["name"],
                    item.get("notes", ""),
                    item.get("deadline"),
                    item.get("is_completed", 0),
                    item.get("sort_order", self._next_sort_order("todo_tasks")),
                    item.get("created_at", datetime.now().isoformat()),
                    new_template_id,
                ),
            )
            if legacy_goal_id is not None:
                try:
                    goal_id_map[int(legacy_goal_id)] = int(cur.lastrowid)
                except (TypeError, ValueError):
                    pass

        # Import milestones, remapping goal_id onto the imported goals.
        for ms in data.get("milestones", []):
            legacy_goal = ms.get("goal_id")
            if legacy_goal is None or not ms.get("title"):
                continue
            try:
                new_goal_id = goal_id_map.get(int(legacy_goal))
            except (TypeError, ValueError):
                new_goal_id = None
            if not new_goal_id:
                continue
            cur.execute(
                "SELECT id FROM milestones WHERE goal_id = ? "
                "AND title = ? COLLATE NOCASE AND sort_order = ?",
                (new_goal_id, ms["title"], ms.get("sort_order", 0)),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO milestones (goal_id, title, note, is_done, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_goal_id,
                    ms["title"],
                    ms.get("note", ""),
                    ms.get("is_done", 0),
                    ms.get("sort_order", 0),
                    ms.get("created_at", datetime.now().isoformat()),
                ),
            )

        # Settings are part of the authoritative JSON backup. Unknown keys are
        # intentionally retained for forward compatibility.
        for setting in data.get("settings", []):
            key = setting.get("key")
            value = setting.get("value")
            if not isinstance(key, str) or not isinstance(value, str) or not key:
                continue
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

        logger.info(
            "Imported backup: %d subject(s), %d session(s), %d goal(s), "
            "%d milestone(s), %d template(s), %d setting(s)",
            len(subjects), len(data.get("sessions", [])), len(data.get("todo_tasks", [])),
            len(data.get("milestones", [])), len(data.get("goal_templates", [])),
            len(data.get("settings", [])),
        )


# Shared singleton — production code imports this. Tests construct their own
# Database(tmp_path) instead and never rely on this object.
db = Database()
