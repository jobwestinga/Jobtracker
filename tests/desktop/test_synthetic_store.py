"""The shared synthetic comparison store is deterministic and engine-readable."""
import sqlite3
from datetime import datetime

from tests.support import synthetic_store
from tests.support.desktop import InProcessEngine


def _uids(path):
    with sqlite3.connect(path) as connection:
        return {table: sorted(row[0] for row in connection.execute(f"SELECT uid FROM {table}"))
                for table in ("tasks", "sessions", "todo_tasks", "milestones", "goal_templates")}


def test_store_is_repeatable_with_stable_identities(tmp_path):
    first = synthetic_store.build(tmp_path / "a.db", foreign_timer=True)
    second = synthetic_store.build(tmp_path / "b.db", foreign_timer=True)
    assert first == second
    a, b = _uids(tmp_path / "a.db"), _uids(tmp_path / "b.db")
    assert a["tasks"] == b["tasks"] and a["todo_tasks"] == b["todo_tasks"] and a["milestones"] == b["milestones"]


def test_store_covers_the_comparison_cases(tmp_path):
    from jobtracker.core.database import Database

    synthetic_store.build(tmp_path / "s.db", foreign_timer=True)
    database = Database(tmp_path / "s.db")
    try:
        engine = InProcessEngine(database, tmp_path, recover=False)
        subjects = engine.ok("subjects.list")["subjects"]
        assert any(len(s["name"]) > 40 for s in subjects)
        assert engine.ok("subjects.list", {"archived": True})["subjects"][0]["name"] == "Old project"
        goals = engine.ok("goals.list")["goals"]
        assert [g["name"] for g in goals][:2] == ["Submit thesis chapter", "Read four books"]
        assert goals[0]["progress"] == [1, 3] and goals[0]["focused"] is True
        timer = engine.ok("timer.get")
        assert timer["active"] is not None and len(timer["foreign_open"]) == 1
        starts = [row[0] for row in database.connection.execute("SELECT start_time FROM sessions")]
        assert any("." in start for start in starts)  # fractional precision
        assert any(datetime.fromisoformat(start) > synthetic_store.FIXED_NOW for start in starts)
        assert len(engine.ok("weight.list")["measurements"]) > 30
    finally:
        database.connection.close()
