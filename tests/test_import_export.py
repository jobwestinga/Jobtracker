"""JSON backup export/import round trip and duplicate-import idempotency."""

from datetime import datetime

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
    assert set(data.keys()) == {"subjects", "sessions", "todo_tasks"}
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
