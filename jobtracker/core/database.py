"""
SQLite persistence layer for JobTracker.

Tables:
  tasks      — timed subjects (name, color, notes, manual sort order)
  sessions   — task_id -> tasks.id, start/end times, duration, optional note
  todo_tasks — completable tasks with optional deadline and manual sort order
  settings   — key/value store for user preferences
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Optional

from .config import DB_PATH
from .models import Subject, Session, TodoTask


class Database:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
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

        # Migration for sessions.note
        if not self._column_exists("sessions", "note"):
            cur.execute("ALTER TABLE sessions ADD COLUMN note TEXT")

        # Migration for tasks.sort_order
        if not self._column_exists("tasks", "sort_order"):
            cur.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER DEFAULT 0")

        # Migration for tasks.is_archived
        if not self._column_exists("tasks", "is_archived"):
            cur.execute("ALTER TABLE tasks ADD COLUMN is_archived INTEGER DEFAULT 0")

        # Ensure task rows have a deterministic manual order.
        cur.execute(
            "UPDATE tasks SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"
        )

        # Ensure todo rows have a deterministic manual order.
        cur.execute(
            "UPDATE todo_tasks SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"
        )

        # One-time cleanup: remove accidental short sessions (< 30 seconds)
        cur.execute(
            "DELETE FROM sessions WHERE end_time IS NOT NULL AND duration_seconds < 30"
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
        cur.execute(
            "INSERT INTO sessions (task_id, start_time) VALUES (?, ?)",
            (subject_id, start_time),
        )
        self.connection.commit()
        return self.get_session(cur.lastrowid)

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

    def complete_todo_task(self, todo_task_id: int) -> None:
        cur = self.connection.cursor()
        cur.execute(
            "UPDATE todo_tasks SET is_completed = 1 WHERE id = ?", (todo_task_id,)
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

    # ── Backup / Restore ─────────────────────────────────────────────────
    def export_data(self) -> dict:
        cur = self.connection.cursor()
        cur.execute("SELECT * FROM tasks")
        subjects = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM sessions")
        sessions = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM todo_tasks")
        todo_tasks = [dict(row) for row in cur.fetchall()]

        return {
            "subjects": subjects,
            "sessions": sessions,
            "todo_tasks": todo_tasks,
        }

    def import_data(self, data: dict) -> None:
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
                "INSERT INTO tasks (name, color, notes, sort_order, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    subject["name"],
                    subject.get("color", "#3B82F6"),
                    subject.get("notes", ""),
                    subject.get("sort_order", self._next_sort_order("tasks")),
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
                "SELECT id FROM sessions WHERE task_id = ? AND start_time = ? AND end_time = ?",
                (new_subject_id, sess["start_time"], sess.get("end_time")),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO sessions (task_id, start_time, end_time, duration_seconds, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    new_subject_id,
                    sess["start_time"],
                    sess.get("end_time"),
                    sess.get("duration_seconds", 0),
                    sess.get("note"),
                ),
            )

        # Import completable tasks
        for item in data.get("todo_tasks", []):
            if not item.get("name"):
                continue
            cur.execute(
                "SELECT id FROM todo_tasks WHERE name = ? COLLATE NOCASE AND COALESCE(deadline, '') = COALESCE(?, '')",
                (item["name"], item.get("deadline")),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO todo_tasks (name, notes, deadline, is_completed, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item["name"],
                    item.get("notes", ""),
                    item.get("deadline"),
                    item.get("is_completed", 0),
                    item.get("sort_order", self._next_sort_order("todo_tasks")),
                    item.get("created_at", datetime.now().isoformat()),
                ),
            )

        self.connection.commit()


# Shared singleton
db = Database()
