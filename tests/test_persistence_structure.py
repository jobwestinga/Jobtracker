"""One-connection transaction and backup contracts across repositories."""

from datetime import datetime, timedelta
import sqlite3
import uuid

import pytest

from jobtracker.core.database import Database
from jobtracker.services.weight_service import WeightService


def test_entity_writes_are_invisible_until_one_outer_commit(database):
    observer = sqlite3.connect(database.db_path)
    try:
        with database.atomic():
            subject = database.add_subject("Atomic", "#123456", "")
            goal = database.add_todo_task("Goal", "", None)
            database.add_milestone(goal.id, "Milestone")
            database.add_goal_template("Daily", "", "daily", "[]")
            start = datetime(2026, 10, 11, 9)
            database.add_session(subject.id, start, start + timedelta(minutes=75), 4500)
            WeightService(database).upsert("2026-10-11", 82.8)
            database.set_setting("structure_test", "kept")
            for table in ("tasks", "todo_tasks", "milestones", "goal_templates", "sessions", "weight_measurements", "settings"):
                assert observer.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert database.connection.in_transaction
        for table in ("tasks", "todo_tasks", "milestones", "goal_templates", "sessions", "weight_measurements"):
            assert observer.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert observer.execute("SELECT value FROM settings WHERE key = 'structure_test'").fetchone()[0] == "kept"
        assert not database.connection.in_transaction
    finally:
        observer.close()


def test_nested_failure_rolls_back_only_its_repository_changes(database):
    with database.atomic():
        subject = database.add_subject("Outer", "#123456", "")
        with pytest.raises(RuntimeError, match="nested"):
            with database.atomic():
                database.add_todo_task("Inner goal", "", None)
                WeightService(database).upsert("2026-10-11", 82.8)
                raise RuntimeError("nested failure")
        database.set_setting("outer", "committed")
        assert database.get_subject(subject.id) is not None
        assert database.get_all_todo_tasks() == []
        assert WeightService(database).list_measurements() == []
    assert database.get_setting("outer") == "committed"
    assert database.connection._atomic_depth == 0


def test_outer_failure_rolls_back_all_repositories_and_backup_import(database):
    before = database.export_data()
    with pytest.raises(RuntimeError, match="outer"):
        with database.atomic():
            database.add_subject("Unsaved", "#123456", "")
            database.import_data({
                "todo_tasks": [{"id": 1, "name": "Imported goal"}],
                "milestones": [{"goal_id": 1, "title": "Imported milestone"}],
                "weights": [{"measured_at": "2026-10-11", "weight_kg": 82.8}],
                "settings": [{"key": "imported", "value": "yes"}],
            })
            raise RuntimeError("outer failure")
    assert database.export_data() == before
    assert database.connection._atomic_depth == 0
    assert not database.connection.in_transaction


def test_repositories_keep_database_subclass_dispatch(tmp_path):
    calls = []

    class Adapter(Database):
        def get_subject(self, subject_id):
            calls.append(subject_id)
            return super().get_subject(subject_id)

    database = Adapter(tmp_path / "override.db")
    try:
        subject = database.add_subject("Override", "#123456", "")
        assert calls == [subject.id]
        calls.clear()
        database.start_session(subject.id, start_time=datetime(2026, 10, 11, 9))
        assert calls == [subject.id]
    finally:
        database.connection.close()


def test_backup_codec_keeps_weight_alias_and_source_identity(database, tmp_path):
    weights = WeightService(database)
    canonical = weights.upsert("2026-10-11", 82.8)["measurement"]
    alias = str(uuid.uuid4())
    weights.upsert("2026-10-11", 82.9, uid=alias)
    weights.import_rows([{"measured_at": "2009-01-01T09:12:00", "weight_kg": 79.5555, "source": "historic"}])
    backup = database.export_data()
    restored = Database(tmp_path / "restored.db")
    try:
        restored.import_data(backup)
        restored.import_data(backup)
        rows = WeightService(restored).list_measurements()
        assert len(rows) == 2
        assert rows[0]["source"] == "historic"
        assert rows[0]["weight_kg"] == 79.5555
        assert rows[1]["uid"] == canonical["uid"]
        assert WeightService(restored).delete(alias)
        assert len(WeightService(restored).list_measurements()) == 1
    finally:
        restored.connection.close()


def test_schema_reopen_keeps_rows_uids_and_sql_inventory(database):
    subject = database.add_subject("Reopen", "#123456", "")
    before = database.connection.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    reopened = Database(database.db_path)
    try:
        after = reopened.connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
        assert reopened.get_subject(subject.id).uid == subject.uid
    finally:
        reopened.connection.close()
