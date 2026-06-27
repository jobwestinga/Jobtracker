"""Additive schema migration checks against a pre-foundation database."""

import sqlite3

from jobtracker.core.database import Database


def test_legacy_database_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            duration_seconds INTEGER DEFAULT 0,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE todo_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            notes TEXT DEFAULT '',
            deadline TIMESTAMP,
            is_completed INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tasks (name, color) VALUES ('Legacy subject', '#123456');
        INSERT INTO sessions
            (task_id, start_time, end_time, duration_seconds)
            VALUES (1, '2026-06-20T10:00:00', '2026-06-20T10:00:10', 10);
        INSERT INTO todo_tasks (name) VALUES ('Legacy task');
        """
    )
    connection.commit()
    connection.close()

    migrated = Database(path)
    try:
        session_columns = {
            row["name"]
            for row in migrated.connection.execute("PRAGMA table_info(sessions)")
        }
        goal_columns = {
            row["name"]
            for row in migrated.connection.execute("PRAGMA table_info(todo_tasks)")
        }
        tables = {
            row["name"]
            for row in migrated.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        assert {"note", "last_active_at"} <= session_columns
        assert "template_id" in goal_columns
        assert {"milestones", "goal_templates"} <= tables
        indexes = {
            row["name"]
            for row in migrated.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "idx_sessions_start_time",
            "idx_sessions_task_start",
        } <= indexes
        assert migrated.get_subject_by_name("Legacy subject") is not None
        assert migrated.get_todo_task(1).name == "Legacy task"
        # Migration is additive and no longer performs a destructive startup
        # cleanup of historical short rows.
        assert migrated.get_session(1).duration_seconds == 10
    finally:
        migrated.connection.close()


def test_existing_templates_gain_default_schedule_days(tmp_path):
    path = tmp_path / "legacy_templates.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE goal_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT DEFAULT '',
            recurrence TEXT NOT NULL DEFAULT 'daily',
            milestones_json TEXT DEFAULT '[]',
            last_generated TEXT,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO goal_templates (title, recurrence)
            VALUES ('Daily', 'daily'), ('Weekly', 'weekly'), ('Monthly', 'monthly');
        """
    )
    connection.commit()
    connection.close()

    migrated = Database(path)
    try:
        templates = {template.title: template for template in migrated.get_goal_templates()}
        assert templates["Daily"].recurrence_day is None
        assert templates["Weekly"].recurrence_day == 1
        assert templates["Monthly"].recurrence_day == 1
    finally:
        migrated.connection.close()
