"""Idempotent schema initialization; existing table names and migrations remain intact."""

from __future__ import annotations

import logging
import sqlite3

from .. import sync_policy

logger = logging.getLogger("jobtracker")


class SchemaManager:
    """Schema setup on the same connection used by every repository."""

    def __init__(self, database):
        self._database = database

    def _init_db(self) -> None:
        cur = self._database.connection.cursor()

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
                recurrence_day INTEGER,
                milestones_json TEXT DEFAULT '[]',
                last_generated TEXT,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Weight history is independent of timed subjects and uses calendar
        # dates. Adding a table leaves every existing row and schema intact.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weight_measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                measured_at TEXT NOT NULL,
                weight_kg REAL NOT NULL CHECK(weight_kg > 0 AND weight_kg <= 1000),
                source TEXT NOT NULL DEFAULT 'manual'
                    CHECK(source IN ('manual', 'scale', 'historic')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_weights_measured_at "
            "ON weight_measurements(measured_at)"
        )

        # Migration: DROP sessions.note.
        #
        # Session notes were removed from the product — they were never used and
        # the UI no longer offers the field. This is the one NON-ADDITIVE
        # migration in the schema, so it is deliberately loud: any non-empty note
        # is written to the log before the column goes, and the drop only runs
        # while the column still exists (idempotent on every later launch).
        # Subject notes (`tasks.notes`) and milestone notes are unaffected.
        if self._database._column_exists("sessions", "note"):
            cur.execute(
                "SELECT id, note FROM sessions "
                "WHERE note IS NOT NULL AND TRIM(note) <> ''"
            )
            stranded = cur.fetchall()
            for row in stranded:
                logger.warning(
                    "Dropping sessions.note: session %s had note %r",
                    row["id"],
                    row["note"],
                )
            logger.info(
                "Migration: dropping sessions.note (%d non-empty note(s))",
                len(stranded),
            )
            try:
                cur.execute("ALTER TABLE sessions DROP COLUMN note")
            except sqlite3.OperationalError:
                # DROP COLUMN needs SQLite >= 3.35. On anything older the column
                # simply stays: nothing reads or writes it, so leaving it is far
                # better than refusing to start.
                logger.warning(
                    "Could not drop sessions.note (SQLite %s); leaving it unused",
                    sqlite3.sqlite_version,
                )

        # Migration for tasks.sort_order
        if not self._database._column_exists("tasks", "sort_order"):
            cur.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER DEFAULT 0")

        # Migration for tasks.is_archived
        if not self._database._column_exists("tasks", "is_archived"):
            cur.execute("ALTER TABLE tasks ADD COLUMN is_archived INTEGER DEFAULT 0")

        # Migration for sessions.last_active_at — a single nullable timestamp per
        # session, heartbeated ~once/minute while the session is active. This is
        # additive and reversible (the column can be ignored or dropped without
        # affecting any existing read path; historical rows simply hold NULL).
        if not self._database._column_exists("sessions", "last_active_at"):
            cur.execute("ALTER TABLE sessions ADD COLUMN last_active_at TIMESTAMP")

        # Migration for todo_tasks.template_id — which recurring template (if any)
        # produced this Goal instance. Additive, nullable, reversible.
        if not self._database._column_exists("todo_tasks", "template_id"):
            cur.execute("ALTER TABLE todo_tasks ADD COLUMN template_id INTEGER")

        # Weekly-focus flag for goals (user highlights the goals they are
        # concentrating on right now). Additive, reversible; 0 = not focused.
        if not self._database._column_exists("todo_tasks", "is_focused"):
            cur.execute(
                "ALTER TABLE todo_tasks ADD COLUMN is_focused INTEGER DEFAULT 0"
            )

        # Scheduled weekday/month-day for recurring templates. Existing weekly
        # and monthly templates retain predictable period-start behavior.
        if not self._database._column_exists("goal_templates", "recurrence_day"):
            cur.execute("ALTER TABLE goal_templates ADD COLUMN recurrence_day INTEGER")
            cur.execute(
                "UPDATE goal_templates SET recurrence_day = 1 "
                "WHERE recurrence IN ('weekly', 'monthly')"
            )

        # ── Sync identity (additive, reversible) ─────────────────────────
        #
        # Every shared row gets a ``uid``: the identity that travels between
        # this machine and the server. Integer ids stay exactly as they are and
        # remain private to this database (see core/sync_policy.py). Dropping
        # the sync feature later would leave these columns unused, not broken.
        for table in sync_policy.SYNCED_TABLES:
            if not self._database._column_exists(table, "uid"):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")
            # Backfill rows that predate the column, then guarantee uniqueness.
            # Done in one UPDATE per table rather than row-by-row in Python:
            # 1200+ sessions would otherwise be 1200 round trips on first launch.
            cur.execute(
                f"UPDATE {table} SET uid = {sync_policy.UUID4_SQL} WHERE uid IS NULL"
            )
            cur.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid ON {table}(uid)"
            )
            # Mint a uid for every future insert, whoever writes it. A trigger
            # rather than 10 edited INSERT statements: no write path can forget,
            # including `import_data()` and any code added later.
            cur.execute(sync_policy.uid_trigger_sql(table))

        # Offline clients can submit a new uid for an already saved day's
        # manual weighing. Keep that alias so its queued edit/delete still finds
        # the canonical row. Aliases are server bookkeeping, not synced rows.
        cur.execute(
            """CREATE TABLE IF NOT EXISTS weight_uid_aliases (
                alias_uid TEXT PRIMARY KEY,
                weight_uid TEXT NOT NULL REFERENCES weight_measurements(uid) ON DELETE CASCADE
            )"""
        )

        # Which install owns a running session. Only the owning device may
        # auto-close it during crash recovery, so the Mac can never end a timer
        # the phone is running. NULL for historical rows and imported backups.
        if not self._database._column_exists("sessions", "device_id"):
            cur.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT")

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

        self._database.connection.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        cur = self._database.connection.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return any(row["name"] == column for row in cur.fetchall())
