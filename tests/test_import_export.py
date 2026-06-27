"""JSON backup export/import round trip and duplicate-import idempotency."""

from datetime import datetime

import pytest

from jobtracker.core.database import Database


def _seed(service):
    s = service.add_subject("Physics", "#3B82F6", "core notes")
    service.add_session(s.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0), note="n1")
    service.add_session(s.id, datetime(2026, 6, 20, 11, 0), datetime(2026, 6, 20, 12, 30))
    service.add_todo_task("Write report", "", "2026-07-01")
    return s


def test_export_shape(service):
    _seed(service)
    data = service.export_data()
    assert set(data.keys()) == {
        "subjects", "sessions", "todo_tasks", "milestones", "goal_templates",
        "settings",
    }
    assert len(data["subjects"]) == 1
    assert len(data["sessions"]) == 2
    assert len(data["todo_tasks"]) == 1


def test_round_trip_into_fresh_db(service, tmp_path):
    _seed(service)
    data = service.export_data()

    other = Database(tmp_path / "restore.db")
    other.import_data(data)

    assert len(other.get_all_subjects_including_archived()) == 1
    restored_subject = other.get_all_subjects_including_archived()[0]
    assert restored_subject.name == "Physics"
    assert len(other.get_sessions_for_subject(restored_subject.id)) == 2
    assert other.get_incomplete_todo_count() == 1
    other.connection.close()


def test_duplicate_import_is_idempotent(service, tmp_path):
    _seed(service)
    data = service.export_data()

    other = Database(tmp_path / "restore.db")
    other.import_data(data)
    other.import_data(data)  # second import of the same backup

    subjects = other.get_all_subjects_including_archived()
    assert len(subjects) == 1  # no duplicate subject
    assert len(other.get_sessions_for_subject(subjects[0].id)) == 2  # no duplicate sessions
    assert other.get_incomplete_todo_count() == 1  # no duplicate todo
    other.connection.close()


def test_goals_milestones_templates_round_trip(service, tmp_path):
    g = service.add_todo_task("Become FIDE master", "outcome goal", None)
    service.add_milestone(g.id, "Reach 2000", "Build a stable rating first.")
    service.add_milestone(g.id, "Reach 2200")
    service.add_goal_template(
        "Weekly routine",
        "",
        "weekly",
        [
            {"title": "Cube", "note": "Practice lookahead."},
            {"title": "Chess", "note": "Review one serious game."},
        ],
        recurrence_day=3,
    )

    data = service.export_data()
    other = Database(tmp_path / "restore.db")
    other.import_data(data)

    goals = other.get_all_todo_tasks()
    assert len(goals) == 1
    restored_milestones = other.get_milestones(goals[0].id)
    assert len(restored_milestones) == 2
    assert restored_milestones[0].note == "Build a stable rating first."
    templates = other.get_goal_templates()
    assert len(templates) == 1
    assert templates[0].recurrence == "weekly"
    assert templates[0].recurrence_day == 3
    assert "Practice lookahead." in templates[0].milestones_json
    other.connection.close()


def test_restore_preserves_repeated_generated_goals_and_template_links(service, tmp_path):
    template = service.add_goal_template("Daily routine", "", "daily", ["Cube"])
    first_id = service.generate_due_goal_instances(
        now=datetime(2026, 6, 20, 10, 0)
    )[0]
    second_id = service.generate_due_goal_instances(
        now=datetime(2026, 6, 21, 10, 0)
    )[0]
    assert first_id != second_id

    data = service.export_data()
    other = Database(tmp_path / "generated_restore.db")
    other.import_data(data)
    restored = other.get_all_todo_tasks()
    assert len(restored) == 2
    assert all(g.template_id is not None for g in restored)
    assert {g.template_id for g in restored} == {other.get_goal_templates()[0].id}

    # Re-importing the same backup remains idempotent.
    other.import_data(data)
    assert len(other.get_all_todo_tasks()) == 2
    assert len(other.get_goal_templates()) == 1
    other.connection.close()


def test_restore_preserves_archive_and_open_session_heartbeat(service, tmp_path):
    subject = service.add_subject("Archived", "#123456", "")
    service.archive_subject(subject.id)
    session = service.db.start_session(subject.id)
    heartbeat = datetime(2026, 6, 20, 12, 0).isoformat()
    service.db.touch_active_session(session.id, heartbeat)

    other = Database(tmp_path / "state_restore.db")
    other.import_data(service.export_data())
    restored_subject = other.get_subject_by_name("Archived")
    restored_open = other.get_open_session()
    assert restored_subject.is_archived == 1
    assert restored_open.last_active_at == heartbeat

    # NULL end times must also deduplicate on repeated import.
    other.import_data(service.export_data())
    assert len(other.get_open_sessions()) == 1
    other.connection.close()


def test_restore_preserves_settings(service, tmp_path):
    service.set_day_start("05:30")
    service.db.set_setting("theme_palette", "Forest")

    other = Database(tmp_path / "settings_restore.db")
    other.import_data(service.export_data())
    assert other.get_setting("day_start_time") == "05:30"
    assert other.get_setting("theme_palette") == "Forest"
    other.connection.close()


def test_malformed_import_rolls_back_all_rows(service, tmp_path):
    service.add_subject("Inserted before failure", "#654321", "")
    payload = service.export_data()
    payload["subjects"].append(
        {
            "id": 999,
            "name": "Would otherwise be inserted",
            "color": "#123456",
            "sort_order": {"invalid": "sqlite binding"},
        }
    )

    other = Database(tmp_path / "rollback.db")
    with pytest.raises(Exception):
        other.import_data(payload)
    assert other.get_all_subjects_including_archived() == []
    assert other.get_all_todo_tasks() == []
    other.connection.close()


def test_import_legacy_tasks_key(service, tmp_path):
    # Older backups stored subjects under a "tasks" key; import must still map them.
    legacy = {
        "tasks": [{"id": 5, "name": "Legacy Subject", "color": "#EF4444", "notes": ""}],
        "sessions": [
            {
                "task_id": 5,
                "start_time": "2026-06-20T09:00:00",
                "end_time": "2026-06-20T10:00:00",
                "duration_seconds": 3600,
            }
        ],
        "todo_tasks": [],
    }
    other = Database(tmp_path / "legacy.db")
    other.import_data(legacy)
    subjects = other.get_all_subjects_including_archived()
    assert len(subjects) == 1
    assert subjects[0].name == "Legacy Subject"
    assert len(other.get_sessions_for_subject(subjects[0].id)) == 1
    other.connection.close()
