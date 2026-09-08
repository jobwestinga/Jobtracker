"""Cross-machine row identity (``uid``) and device ownership of sessions.

These cover the two invariants the server work rests on:

* every shared row has a stable ``uid``, minted by whatever code path created it
  and preserved across a backup round-trip;
* an open session belongs to the device that started it, and no other device's
  crash recovery may close it.
"""

import re
import uuid
from datetime import datetime

import pytest

from jobtracker.core import sync_policy
from jobtracker.core.database import Database
from jobtracker.services.tracker_service import TrackerService

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _all_uids(database, table):
    cur = database.connection.cursor()
    cur.execute(f"SELECT uid FROM {table}")
    return [row["uid"] for row in cur.fetchall()]


def _seed_one_of_everything(service):
    """One row in each synced table, created through the normal service API."""
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(
        subject.id,
        datetime(2026, 6, 1, 10, 0, 0),
        datetime(2026, 6, 1, 11, 0, 0),
    )
    goal = service.add_todo_task("Pass the exam", "", None)
    milestone = service.add_milestone(goal.id, "Read chapter 1")
    template = service.add_goal_template("Daily review", "", "daily")
    return subject, session, goal, milestone, template


# ── uid is minted for every row, by every write path ────────────────────


def test_every_synced_table_gets_a_uid(service, database):
    _seed_one_of_everything(service)
    for table in sync_policy.SYNCED_TABLES:
        uids = _all_uids(database, table)
        assert uids, f"{table} had no rows to check"
        for value in uids:
            assert value is not None, f"{table} row was left without a uid"
            assert UUID4_RE.match(value), f"{table} uid {value!r} is not a UUID4"


def test_uids_are_unique_within_a_table(service, database):
    subject = service.add_subject("Physics", "#3B82F6", "")
    for hour in range(10, 20):
        service.add_session(
            subject.id,
            datetime(2026, 6, 1, hour, 0, 0),
            datetime(2026, 6, 1, hour, 30, 0),
        )
    uids = _all_uids(database, "sessions")
    assert len(uids) == 10
    assert len(set(uids)) == 10


def test_uid_unique_index_rejects_a_duplicate(service, database):
    subject = service.add_subject("Physics", "#3B82F6", "")
    existing = database.get_subject(subject.id).uid
    other = service.add_subject("Maths", "#EF4444", "")
    with pytest.raises(Exception):
        database.connection.execute(
            "UPDATE tasks SET uid = ? WHERE id = ?", (existing, other.id)
        )
        database.connection.commit()


def test_explicit_uid_is_kept_not_overwritten(database):
    """The trigger only fills a MISSING uid — that is what lets a restore keep
    identity instead of forking it."""
    chosen = str(uuid.uuid4())
    cur = database.connection.cursor()
    cur.execute(
        "INSERT INTO tasks (name, color, notes, created_at, uid) "
        "VALUES ('Chemistry', '#10B981', '', '2026-06-01T09:00:00', ?)",
        (chosen,),
    )
    database.connection.commit()
    assert database.get_subject(cur.lastrowid).uid == chosen


def test_rows_predating_the_column_are_backfilled_on_open(tmp_path, service):
    """Simulates a database written before uid existed: NULL uids must be filled
    the next time the app opens the file."""
    path = tmp_path / "backfill.db"
    db = Database(path)
    try:
        svc = TrackerService(db)
        _seed_one_of_everything(svc)
        for table in sync_policy.SYNCED_TABLES:
            db.connection.execute(f"UPDATE {table} SET uid = NULL")
        db.connection.commit()
        assert _all_uids(db, "sessions") == [None]
    finally:
        db.connection.close()

    reopened = Database(path)
    try:
        for table in sync_policy.SYNCED_TABLES:
            for value in _all_uids(reopened, table):
                assert value is not None and UUID4_RE.match(value)
    finally:
        reopened.connection.close()


# ── uid survives the backup round-trip ─────────────────────────────────


def test_uid_survives_export_import_roundtrip(tmp_path, database, service):
    subject, session, goal, milestone, template = _seed_one_of_everything(service)
    before = {
        table: sorted(v for v in _all_uids(database, table) if v)
        for table in sync_policy.SYNCED_TABLES
    }

    restored = Database(tmp_path / "restored.db")
    try:
        restored.import_data(database.export_data())
        after = {
            table: sorted(v for v in _all_uids(restored, table) if v)
            for table in sync_policy.SYNCED_TABLES
        }
    finally:
        restored.connection.close()

    assert after == before


def test_import_of_a_clashing_uid_still_imports_the_row(tmp_path, database, service):
    """A colliding uid must not abort the restore; the row imports with a new
    identity rather than violating the unique index."""
    service.add_subject("Physics", "#3B82F6", "")
    payload = database.export_data()
    clashing = payload["subjects"][0]["uid"]

    target = Database(tmp_path / "target.db")
    try:
        target_svc = TrackerService(target)
        squatter = target_svc.add_subject("Something else", "#EF4444", "")
        target.connection.execute(
            "UPDATE tasks SET uid = ? WHERE id = ?", (clashing, squatter.id)
        )
        target.connection.commit()

        target.import_data(payload)

        names = {s.name for s in target.get_all_subjects_including_archived()}
        assert "Physics" in names
        uids = [v for v in _all_uids(target, "tasks") if v]
        assert len(uids) == len(set(uids))
    finally:
        target.connection.close()


# ── device identity ────────────────────────────────────────────────────


def test_device_id_is_stable_across_calls(database):
    first = database.get_or_create_device_id()
    assert first
    assert database.get_or_create_device_id() == first


def test_device_id_is_not_copied_by_a_restore(tmp_path, database, service):
    """Two installs sharing a device_id would both claim the same running timer,
    so the identity must not ride along in a backup."""
    source_device = database.get_or_create_device_id()
    service.add_subject("Physics", "#3B82F6", "")

    target = Database(tmp_path / "target.db")
    try:
        target_device = target.get_or_create_device_id()
        target.import_data(database.export_data())
        assert target.get_setting(sync_policy.DEVICE_ID_SETTING) == target_device
        assert target_device != source_device
    finally:
        target.connection.close()


def test_started_session_records_the_owning_device(service, database):
    subject = service.add_subject("Physics", "#3B82F6", "")
    assert service.start_subject(subject.id) is True
    open_session = database.get_open_sessions()[0]
    assert open_session.device_id == service.device_id


# ── recovery is scoped to the device that owns the session ──────────────


def _open_session_for(database, subject_id, device_id, start_iso):
    cur = database.connection.cursor()
    cur.execute(
        "INSERT INTO sessions (task_id, start_time, last_active_at, device_id) "
        "VALUES (?, ?, ?, ?)",
        (subject_id, start_iso, start_iso, device_id),
    )
    database.connection.commit()
    return cur.lastrowid


def test_recovery_leaves_another_devices_session_running(database, service):
    """The Mac starting up must never end a timer the phone is running."""
    subject = service.add_subject("Physics", "#3B82F6", "")
    phone_session = _open_session_for(
        database, subject.id, "iphone-device", "2026-06-01T10:00:00"
    )

    recovered = TrackerService(database, device_id="macbook-device")

    assert database.get_session(phone_session).end_time is None
    assert recovered.active_session is None


def test_recovery_closes_only_its_own_stale_sessions(database, service):
    subject = service.add_subject("Physics", "#3B82F6", "")
    mine_old = _open_session_for(
        database, subject.id, "macbook-device", "2026-06-01T08:00:00"
    )
    mine_new = _open_session_for(
        database, subject.id, "macbook-device", "2026-06-01T09:00:00"
    )
    theirs = _open_session_for(
        database, subject.id, "iphone-device", "2026-06-01T10:00:00"
    )

    recovered = TrackerService(database, device_id="macbook-device")

    assert database.get_session(mine_old).end_time is not None
    assert database.get_session(mine_new).end_time is None
    assert database.get_session(theirs).end_time is None
    assert recovered.active_session.id == mine_new


def test_unowned_sessions_are_still_adopted(database, service):
    """Rows written before device_id existed have no owner; the app must keep
    recovering them exactly as it did before."""
    subject = service.add_subject("Physics", "#3B82F6", "")
    legacy = _open_session_for(database, subject.id, None, "2026-06-01T09:00:00")

    recovered = TrackerService(database, device_id="macbook-device")

    assert recovered.active_session is not None
    assert recovered.active_session.id == legacy


def test_recover_false_closes_nothing_but_still_adopts(database, service):
    """A service that is only answering a query must never close a session."""
    subject = service.add_subject("Physics", "#3B82F6", "")
    older = _open_session_for(
        database, subject.id, "macbook-device", "2026-06-01T08:00:00"
    )
    newer = _open_session_for(
        database, subject.id, "macbook-device", "2026-06-01T09:00:00"
    )

    passive = TrackerService(database, device_id="macbook-device", recover=False)

    assert database.get_session(older).end_time is None
    assert database.get_session(newer).end_time is None
    assert passive.active_session.id == newer
    assert passive.active_subject.id == subject.id
