"""Opening a database must never lose data or refuse to start.

The migration path is the one piece of code that runs against files written by
every past version of the app, including ones that no longer exist. It has to be
idempotent, additive, and unbothered by a database that is already half-migrated
or slightly odd.

Also covers the backup round trip, since a backup that cannot be restored is not
a backup.
"""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from jobtracker.core import sync_policy
from jobtracker.core.auto_backup import write_auto_backup
from jobtracker.core.database import Database
from jobtracker.services.tracker_service import TrackerService


def open_twice(path):
    """Open, close, reopen — the shape of every real launch after the first."""
    first = Database(path)
    first.connection.close()
    return Database(path)


# ── idempotence ─────────────────────────────────────────────────────────


def test_opening_a_fresh_database_repeatedly_is_stable(tmp_path):
    path = tmp_path / "repeat.db"
    fingerprints = []
    for _ in range(5):
        db = Database(path)
        cur = db.connection.cursor()
        schema = sorted(
            row["sql"] or ""
            for row in cur.execute("SELECT sql FROM sqlite_master ORDER BY name")
        )
        fingerprints.append(schema)
        db.connection.close()
    assert all(f == fingerprints[0] for f in fingerprints)


def test_reopening_never_disturbs_existing_rows(tmp_path):
    path = tmp_path / "rows.db"
    db = Database(path)
    svc = TrackerService(db)
    subject = svc.add_subject("Physics", "#3B82F6", "")
    now = datetime.now().replace(microsecond=0)
    svc.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))
    goal = svc.add_todo_task("A goal", "", None)
    svc.add_milestone(goal.id, "Step")
    before = {
        table: sorted(r["uid"] for r in db.connection.execute(f"SELECT uid FROM {table}"))
        for table in sync_policy.SYNCED_TABLES
    }
    db.connection.close()

    for _ in range(3):
        reopened = Database(path)
        after = {
            table: sorted(
                r["uid"] for r in reopened.connection.execute(f"SELECT uid FROM {table}")
            )
            for table in sync_policy.SYNCED_TABLES
        }
        assert after == before
        reopened.connection.close()


# ── coming from an older schema ─────────────────────────────────────────


def raw_legacy_db(path):
    """A database as an early version of the app would have written it."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, color TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            duration_seconds INTEGER DEFAULT 0,
            note TEXT
        );
        CREATE TABLE todo_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, notes TEXT DEFAULT '',
            deadline TIMESTAMP, is_completed INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tasks (name, color) VALUES ('Old Physics', '#3B82F6');
        INSERT INTO sessions (task_id, start_time, end_time, duration_seconds, note)
        VALUES (1, '2026-01-01T10:00:00', '2026-01-01T11:00:00', 3600, 'a note');
        INSERT INTO todo_tasks (name) VALUES ('Old goal');
        """
    )
    conn.commit()
    conn.close()


def test_an_old_database_opens_and_keeps_its_data(tmp_path):
    path = tmp_path / "legacy.db"
    raw_legacy_db(path)

    db = Database(path)
    try:
        subjects = db.get_all_subjects_including_archived()
        assert [s.name for s in subjects] == ["Old Physics"]
        assert len(db.get_sessions_for_subject(subjects[0].id)) == 1
        assert [g.name for g in db.get_all_todo_tasks()] == ["Old goal"]
    finally:
        db.connection.close()


def test_an_old_database_gains_every_new_column(tmp_path):
    path = tmp_path / "legacy2.db"
    raw_legacy_db(path)
    db = Database(path)
    try:
        def columns(table):
            return {r["name"] for r in db.connection.execute(f"PRAGMA table_info({table})")}

        assert "uid" in columns("tasks")
        assert "is_archived" in columns("tasks")
        assert "sort_order" in columns("tasks")
        assert {"uid", "device_id", "last_active_at"} <= columns("sessions")
        assert {"uid", "template_id", "is_focused"} <= columns("todo_tasks")
        assert {"uid", "recurrence_day"} <= columns("goal_templates")
    finally:
        db.connection.close()


def test_the_old_session_note_column_is_dropped(tmp_path):
    path = tmp_path / "legacy3.db"
    raw_legacy_db(path)
    db = Database(path)
    try:
        columns = {r["name"] for r in db.connection.execute("PRAGMA table_info(sessions)")}
        assert "note" not in columns
    finally:
        db.connection.close()


def test_old_rows_are_given_identities(tmp_path):
    path = tmp_path / "legacy4.db"
    raw_legacy_db(path)
    db = Database(path)
    try:
        for table in ("tasks", "sessions", "todo_tasks"):
            uids = [r["uid"] for r in db.connection.execute(f"SELECT uid FROM {table}")]
            assert uids and all(uids)
    finally:
        db.connection.close()


def test_a_half_migrated_database_finishes_cleanly(tmp_path):
    """Interrupted upgrade: some columns present, others not."""
    path = tmp_path / "half.db"
    raw_legacy_db(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE tasks ADD COLUMN uid TEXT")
    conn.execute("ALTER TABLE tasks ADD COLUMN is_archived INTEGER DEFAULT 0")
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        subject = db.get_all_subjects_including_archived()[0]
        assert subject.uid
        assert subject.is_archived == 0
    finally:
        db.connection.close()


def test_uids_already_present_are_left_alone(tmp_path):
    """A second launch must not renumber identities the server already knows."""
    path = tmp_path / "keepuid.db"
    db = Database(path)
    svc = TrackerService(db)
    subject = svc.add_subject("Physics", "#3B82F6", "")
    original = db.uid_for_id("tasks", subject.id)
    db.connection.close()

    reopened = Database(path)
    try:
        assert reopened.uid_for_id("tasks", subject.id) == original
    finally:
        reopened.connection.close()


# ── identity plumbing ───────────────────────────────────────────────────


def test_id_for_uid_rejects_a_table_that_is_not_shared(database):
    with pytest.raises(ValueError):
        database.id_for_uid("settings", "anything")
    with pytest.raises(ValueError):
        database.uid_for_id("sqlite_master", 1)


@pytest.mark.parametrize("junk", ["", "   ", None, 12345])
def test_id_for_uid_handles_nonsense_without_raising(database, junk):
    assert database.id_for_uid("tasks", junk) is None


def test_uid_for_a_missing_row_is_none(database):
    assert database.uid_for_id("tasks", 999999) is None
    assert database.uid_for_id("tasks", None) is None


def test_every_synced_table_has_a_uid_trigger(database):
    triggers = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    for table in sync_policy.SYNCED_TABLES:
        assert sync_policy.uid_trigger_name(table) in triggers


# ── backups ─────────────────────────────────────────────────────────────


def test_a_backup_restores_into_an_empty_database(tmp_path, database, service):
    subject = service.add_subject("Physics", "#3B82F6", "notes")
    now = datetime.now().replace(microsecond=0)
    service.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))
    goal = service.add_todo_task("A goal", "why", None)
    service.add_milestone(goal.id, "Step one")
    service.add_goal_template("Daily", "", "daily")

    payload = database.export_data()
    restored = Database(tmp_path / "restored.db")
    try:
        restored.import_data(payload)
        assert [s.name for s in restored.get_all_subjects_including_archived()] == ["Physics"]
        assert len(restored.get_all_todo_tasks()) == 1
        assert len(restored.get_goal_templates()) == 1
        subject_id = restored.get_all_subjects_including_archived()[0].id
        assert len(restored.get_sessions_for_subject(subject_id)) == 1
    finally:
        restored.connection.close()


def test_restoring_the_same_backup_twice_does_not_duplicate(tmp_path, database, service):
    subject = service.add_subject("Physics", "#3B82F6", "")
    now = datetime.now().replace(microsecond=0)
    service.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))
    payload = database.export_data()

    restored = Database(tmp_path / "twice.db")
    try:
        restored.import_data(payload)
        restored.import_data(payload)
        assert len(restored.get_all_subjects_including_archived()) == 1
        subject_id = restored.get_all_subjects_including_archived()[0].id
        assert len(restored.get_sessions_for_subject(subject_id)) == 1
    finally:
        restored.connection.close()


def test_a_malformed_backup_leaves_the_database_untouched(database, service):
    service.add_subject("Physics", "#3B82F6", "")
    before = len(database.get_all_subjects_including_archived())

    with pytest.raises(Exception):
        database.import_data({"subjects": [{"name": "Fine"}], "sessions": "not a list"})

    assert len(database.get_all_subjects_including_archived()) == before


def test_an_empty_backup_is_accepted(database):
    database.import_data({})
    assert database.get_all_subjects_including_archived() == []


def test_the_auto_backup_writes_readable_json(tmp_path, database, service):
    service.add_subject("Physics", "#3B82F6", "")
    write_auto_backup(database.export_data(), tmp_path)

    files = list(tmp_path.glob("autobackup_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert [s["name"] for s in payload["subjects"]] == ["Physics"]


def test_auto_backups_are_pruned_to_the_newest_ten(tmp_path, database, service):
    service.add_subject("Physics", "#3B82F6", "")
    payload = database.export_data()
    for _ in range(15):
        write_auto_backup(payload, tmp_path)
    assert len(list(tmp_path.glob("autobackup_*.json"))) <= 10


def test_pruning_only_ever_touches_its_own_files(tmp_path, database, service):
    """A user's own file in the backups folder must survive."""
    service.add_subject("Physics", "#3B82F6", "")
    keep = tmp_path / "my_important_export.json"
    keep.write_text("{}")
    payload = database.export_data()
    for _ in range(15):
        write_auto_backup(payload, tmp_path)
    assert keep.exists()


def test_a_backup_survives_a_full_round_trip_through_the_wire_format(
    tmp_path, database, service
):
    """export -> JSON text -> parse -> import, as an actual file would."""
    subject = service.add_subject("Physics", "#3B82F6", "")
    now = datetime.now().replace(microsecond=0)
    service.add_session(subject.id, now - timedelta(hours=3), now - timedelta(hours=1))

    text = json.dumps(database.export_data(), default=str)
    restored = Database(tmp_path / "wire.db")
    try:
        restored.import_data(json.loads(text))
        subject_id = restored.get_all_subjects_including_archived()[0].id
        sessions = restored.get_sessions_for_subject(subject_id)
        assert len(sessions) == 1
        assert sessions[0].duration_seconds == 7200
    finally:
        restored.connection.close()
