"""Adversarial offline, replay, crash and concurrent-edit regressions."""

from datetime import datetime, timedelta
import random

import pytest

from jobtracker.core import sync_policy
from jobtracker.core.database import Database
from jobtracker.sync import state
from jobtracker.sync.client import SyncError
from jobtracker.sync.engine import SyncResult
from jobtracker.sync.service import SyncedTrackerService
from tests.test_sync_engine import world, server_add_subject
from tests.test_sync_service import synced


def test_seventy_five_minute_offline_timer_preserves_original_times(synced):
    subject = synced.svc.add_subject("Offline timer", "#123456", "")
    start = datetime(2026, 6, 1, 10)
    end = start + timedelta(minutes=75)
    assert synced.svc.start_subject(subject.id, start)
    uid = synced.svc.active_session.uid
    synced.svc.stop_active_subject(end)

    result = synced.engine.sync()

    assert result.ok, result.error
    row = synced.server_db.connection.execute(
        "SELECT * FROM sessions WHERE uid = ?", (uid,)
    ).fetchone()
    assert row is not None
    assert row["start_time"] == start.isoformat()
    assert row["end_time"] == end.isoformat()
    assert row["duration_seconds"] == 4500
    assert not result.repaired


def test_more_than_one_batch_drains_before_pulling(synced):
    subject = synced.svc.add_subject("Initial", "#123456", "")
    assert synced.engine.sync().ok
    for index in range(207):
        synced.svc.update_subject(subject.id, f"Edit {index}", "#123456", "")

    result = synced.engine.sync()

    assert result.ok, result.error
    assert result.pushed == 207
    assert state.pending_count(synced.mirror.connection) == 0
    assert synced.mirror.get_subject(subject.id).name == "Edit 206"
    assert synced.server_db.get_all_subjects()[0].name == "Edit 206"
    assert [len(batch["ops"]) for batch in synced.client.sent_batches[-2:]] == [200, 7]


def test_partial_batch_acknowledges_prefix_and_records_actual_failure(world):
    state.enqueue(world.mirror.connection, "add_goal", {"name": "Saved prefix"})
    failed_id = state.enqueue(world.mirror.connection, "no_such_op", {})
    state.enqueue(world.mirror.connection, "add_goal", {"name": "Must wait"})

    result = world.engine.sync()

    assert not result.ok
    assert result.pushed == 1
    pending = state.pending(world.mirror.connection)
    assert len(pending) == 2
    assert pending[0]["op_id"] == failed_id
    assert pending[0]["attempts"] == 1
    assert [goal.name for goal in world.server_db.get_all_todo_tasks()] == ["Saved prefix"]
    second = world.engine.sync()
    assert not second.ok
    assert state.pending(world.mirror.connection)[0]["attempts"] == 2


@pytest.mark.parametrize("kind", ["empty", "out_of_order", "duplicate"])
def test_invalid_acknowledgement_never_pulls_or_discards_unacknowledged_work(world, kind):
    first = state.enqueue(world.mirror.connection, "add_goal", {"name": "First"})
    second = state.enqueue(world.mirror.connection, "add_goal", {"name": "Second"})
    acknowledgements = {
        "empty": [],
        "out_of_order": [{"op_id": second}],
        "duplicate": [{"op_id": first}, {"op_id": first}],
    }
    world.client.send_ops = lambda *args, **kwargs: {"applied": acknowledgements[kind]}
    world.client.pull = lambda *args, **kwargs: pytest.fail("must not pull past unacknowledged work")

    result = world.engine.sync()

    assert not result.ok
    assert state.pending_count(world.mirror.connection) == 2


def test_edit_while_pull_is_in_flight_is_preserved_for_next_pass(synced):
    subject = synced.svc.add_subject("Before", "#123456", "")
    assert synced.engine.sync().ok
    cursor = state.last_seq(synced.mirror.connection)
    real_pull = synced.client.pull

    def edit_during_response(*args, **kwargs):
        response = real_pull(*args, **kwargs)
        synced.svc.update_subject(subject.id, "Concurrent local edit", "#123456", "")
        return response

    synced.client.pull = edit_during_response
    result = synced.engine.sync()

    assert result.ok
    assert result.pending == 1
    assert state.last_seq(synced.mirror.connection) == cursor
    assert synced.mirror.get_subject(subject.id).name == "Concurrent local edit"
    synced.client.pull = real_pull
    assert synced.engine.sync().ok
    assert synced.server_db.get_all_subjects()[0].name == "Concurrent local edit"


def test_edit_while_snapshot_downloads_prevents_destructive_repair(synced):
    subject = synced.svc.add_subject("Before", "#123456", "")
    assert synced.engine.sync().ok
    real_snapshot = synced.client.snapshot

    def edit_during_response():
        response = real_snapshot()
        synced.svc.update_subject(subject.id, "Must survive", "#123456", "")
        return response

    synced.client.snapshot = edit_during_response
    result = SyncResult()

    assert not synced.engine.full_resync(result)
    assert not result.repaired
    assert state.pending_count(synced.mirror.connection) == 1
    assert synced.mirror.get_subject(subject.id).name == "Must survive"


def test_full_resync_enforces_its_own_outbox_guard(synced):
    subject = synced.svc.add_subject("Unsent", "#123456", "")
    synced.client.snapshot = lambda: pytest.fail("must not even download while edits wait")
    assert not synced.engine.full_resync(SyncResult())
    assert synced.mirror.get_subject(subject.id).name == "Unsent"


def test_incomplete_snapshot_keeps_all_existing_rows(world):
    uid = server_add_subject(world)
    assert world.engine.sync().ok
    snapshot = world.client.snapshot()
    del snapshot["subjects"]
    world.client.snapshot = lambda: snapshot

    with pytest.raises(SyncError, match="incomplete"):
        world.engine.full_resync(SyncResult())

    assert world.mirror.id_for_uid("tasks", uid) is not None


def test_invalid_snapshot_rolls_back_rows_and_cursor(world):
    uid = server_add_subject(world, "Safe original")
    assert world.engine.sync().ok
    original_id = world.mirror.id_for_uid("tasks", uid)
    cursor = state.last_seq(world.mirror.connection)
    snapshot = world.client.snapshot()
    snapshot["subjects"][0]["name"] = "Should roll back"
    snapshot["subjects"].append({"name": "Missing identity"})
    snapshot["head"] = cursor + 100
    world.client.snapshot = lambda: snapshot
    result = SyncResult()

    with pytest.raises(SyncError, match="identity"):
        world.engine.full_resync(result)

    assert world.mirror.id_for_uid("tasks", uid) == original_id
    assert world.mirror.get_subject(original_id).name == "Safe original"
    assert state.last_seq(world.mirror.connection) == cursor
    assert result.applied == 0
    assert not result.repaired


def test_nonadvancing_feed_cannot_spin_forever(world):
    world.client.pull = lambda since: {"seq": since, "changes": [], "more": True}
    result = world.engine.sync()
    assert not result.ok
    assert "non-advancing" in result.error


def test_restored_server_feed_repairs_rows_with_unchanged_uid_counts(world):
    uid = server_add_subject(world, "Restored server row")
    assert world.engine.sync().ok
    local_id = world.mirror.id_for_uid("tasks", uid)
    world.mirror.connection.execute("UPDATE tasks SET name = 'Newer local mirror' WHERE id = ?", (local_id,))
    world.mirror.connection.commit()
    state.set_value(world.mirror.connection, state.LAST_SEQ, 999)
    result = world.engine.sync()
    assert result.ok, result.error
    assert result.repaired
    assert world.mirror.get_subject(world.mirror.id_for_uid("tasks", uid)).name == "Restored server row"
    assert state.last_seq(world.mirror.connection) < 999


def test_duplicate_and_shift_apply_exactly_once(synced):
    subject = synced.svc.add_subject("Subject", "#123456", "")
    start = datetime(2026, 6, 1, 10)
    original = synced.svc.add_session(subject.id, start, start + timedelta(hours=1))
    copied = synced.svc.duplicate_session(original.id, "next_day")
    shifted = synced.svc.shift_session(original.id, 900)
    queued = state.pending(synced.mirror.connection)
    assert [entry["op"] for entry in queued] == ["add_subject", "add_session", "add_session", "update_session"]

    result = synced.engine.sync()

    assert result.ok, result.error
    assert not result.repaired
    assert synced.server_db.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    server_original = synced.server_db.get_session(synced.server_db.id_for_uid("sessions", original.uid))
    server_copy = synced.server_db.get_session(synced.server_db.id_for_uid("sessions", copied.uid))
    assert server_original.start_time == shifted.start_time
    assert server_copy.start_time == copied.start_time


def test_queue_failure_rolls_back_edit_instead_of_leaving_unsent_data(synced, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(state, "enqueue", fail)

    with pytest.raises(OSError, match="disk failure"):
        synced.svc.add_subject("Must roll back", "#123456", "")

    assert synced.mirror.get_all_subjects() == []
    assert state.pending_count(synced.mirror.connection) == 0
    assert not synced.mirror.connection.in_transaction


def test_queue_failure_when_stopping_restores_running_timer(synced, monkeypatch):
    subject = synced.svc.add_subject("Subject", "#123456", "")
    start = datetime(2026, 6, 1, 10)
    synced.svc.start_subject(subject.id, start)
    uid = synced.svc.active_session.uid
    count = state.pending_count(synced.mirror.connection)
    monkeypatch.setattr(state, "enqueue", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk failure")))

    with pytest.raises(OSError):
        synced.svc.stop_active_subject(start + timedelta(minutes=75))

    assert synced.svc.active_session.uid == uid
    assert synced.svc.active_session.end_time is None
    assert synced.mirror.get_session(synced.svc.active_session.id).end_time is None
    assert state.pending_count(synced.mirror.connection) == count


def test_recovered_short_parallel_session_is_queued_and_retained(tmp_path):
    database = Database(tmp_path / "recovery.db")
    try:
        subject = database.add_subject("Subject", "#123456", "")
        first = database.start_session(subject.id, device_id="macbook", start_time=datetime(2026, 6, 1, 10))
        second = database.start_session(subject.id, device_id="macbook", start_time=datetime(2026, 6, 1, 11))
        service = SyncedTrackerService(database, device_id="macbook")
        queued = state.pending(database.connection)
        assert service.active_session.uid == second.uid
        assert database.get_session(first.id).end_time is not None
        assert database.get_session(first.id).duration_seconds == 0
        assert [entry["op"] for entry in queued] == ["resolve_recovery"]
        assert queued[0]["params"]["session_uid"] == first.uid
    finally:
        database.connection.close()


def test_failed_feed_dependency_repairs_nullable_relationship(world):
    # A nullable FK can otherwise turn a missing parent into silent NULL while
    # counts and UID hashes still agree. Force a parent onto a later page.
    response = world.http.post("/ops", json={"ops": [
        {"op_id": sync_policy.new_uid(), "op": "add_template", "params": {"title": "Daily", "recurrence": "daily"}},
        {"op_id": sync_policy.new_uid(), "op": "generate_due_goals", "params": {}},
    ]})
    assert response.status_code == 200, response.text
    template = response.json()["applied"][0]["result"]["template"]
    response = world.http.post("/ops", json={"ops": [{
        "op_id": sync_policy.new_uid(), "op": "update_template", "params": {"template_uid": template["uid"], "title": "Daily changed", "recurrence": "daily"}
    }]})
    assert response.status_code == 200
    real_pull = world.client.pull
    world.client.pull = lambda since: real_pull(since, limit=1)

    result = world.engine.sync()

    assert result.ok, result.error
    assert result.repaired
    assert world.mirror.connection.execute("SELECT template_id FROM todo_tasks").fetchone()[0] is not None


def test_new_setting_and_page_cursor_roll_back_when_row_apply_fails(world):
    uid = server_add_subject(world)
    assert world.engine.sync().ok
    cursor = state.last_seq(world.mirror.connection)
    snapshot = world.client.snapshot()
    snapshot["sessions"] = [{"uid": sync_policy.new_uid(), "subject_uid": "missing", "start_time": "2026-06-01T10:00:00"}]
    world.client.pull = lambda since: {"seq": cursor + 1, "changes": [{"seq": cursor + 1, "table": "sessions", "op": "upsert", "uid": snapshot["sessions"][0]["uid"], "row": snapshot["sessions"][0]}], "settings": {"day_start_time": "05:00"}}
    world.client.snapshot = lambda: snapshot

    result = world.engine.sync()

    assert not result.ok
    assert state.last_seq(world.mirror.connection) == cursor
    assert world.mirror.id_for_uid("tasks", uid) is not None
    assert world.mirror.get_setting("day_start_time", "03:00") != "05:00"


@pytest.mark.parametrize("seed", [0, 7, 29])
def test_seeded_offline_edits_and_lost_responses_converge_without_data_loss(synced, seed):
    rng = random.Random(seed)
    svc = synced.svc
    subjects = [svc.add_subject(f"Subject {index}", "#123456", "") for index in range(3)]
    goals = [svc.add_todo_task(f"Goal {index}", "", None) for index in range(3)]
    start = datetime(2026, 6, 1, 10)
    for index in range(12):
        svc.add_session(subjects[index % 3].id, start + timedelta(days=index), start + timedelta(days=index, hours=1))
    assert synced.engine.sync().ok
    synced.client.offline = True
    assert synced.engine.sync().offline

    for index in range(115):
        sessions = [session for subject in subjects for session in svc.get_sessions_for_subject(subject.id)]
        action = rng.choice(["add", "duplicate", "shift", "delete", "goal", "subject"])
        if action == "add" or not sessions:
            moment = start + timedelta(days=rng.randrange(60), minutes=rng.randrange(1440))
            svc.add_session(rng.choice(subjects).id, moment, moment + timedelta(minutes=rng.randrange(1, 180)))
        elif action == "duplicate":
            svc.duplicate_session(rng.choice(sessions).id, "next_day")
        elif action == "shift":
            svc.shift_session(rng.choice(sessions).id, rng.choice([-3600, -900, 900, 3600]))
        elif action == "delete":
            svc.delete_session(rng.choice(sessions).id)
        elif action == "goal":
            goal = rng.choice(goals)
            svc.toggle_goal_focused(goal.id)
            svc.update_todo_task(goal.id, f"Goal {goal.id} revision {index}", "offline", None)
        elif action == "subject":
            subject = rng.choice(subjects)
            svc.update_subject(subject.id, f"Subject {subject.id} revision {index}", "#987654", "offline")

    # The first request reaches the server but its response disappears. Every
    # original op_id must survive and the retry must not apply edits twice.
    synced.client.offline = False
    original_send = synced.client.send_ops
    def lose_response(*args, **kwargs):
        original_send(*args, **kwargs)
        raise SyncError("simulated dropped response")
    synced.client.send_ops = lose_response
    lost = synced.engine.sync()
    assert lost.offline
    synced.client.send_ops = original_send
    result = synced.engine.sync()
    assert result.ok, result.error
    assert not result.repaired
    assert state.pending_count(synced.mirror.connection) == 0

    def normalized(database):
        tables = {}
        for table in sync_policy.SYNCED_TABLES:
            rows = []
            for row in database.connection.execute(f"SELECT * FROM {table} ORDER BY uid"):
                wire = sync_policy.to_wire(database, table, dict(row))
                wire.pop("created_at", None)  # server/client creation timestamps
                rows.append(wire)
            tables[table] = rows
        return tables
    assert normalized(synced.mirror) == normalized(synced.server_db)


def test_observer_cannot_see_local_edit_before_its_outbox_receipt(synced, monkeypatch):
    # A second SQLite connection stands in for the background worker. During
    # enqueue, the local row is still invisible there; both become visible at
    # the outer transaction's commit.
    observer = Database(synced.mirror.db_path)
    real_enqueue = state.enqueue
    observed = []
    def observe_then_enqueue(*args, **kwargs):
        observed.append(observer.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        return real_enqueue(*args, **kwargs)
    monkeypatch.setattr(state, "enqueue", observe_then_enqueue)
    try:
        subject = synced.svc.add_subject("Atomic row and receipt", "#123456", "")
        assert observed == [0]
        assert observer.id_for_uid("tasks", subject.uid) is not None
        assert state.pending_count(observer.connection) == 1
    finally:
        observer.connection.close()


@pytest.mark.parametrize("repair", [False, True])
def test_owned_running_heartbeat_survives_server_pull_and_repair(synced, repair):
    subject = synced.svc.add_subject("Long running timer", "#123456", "")
    start = datetime.now() - timedelta(minutes=75)
    assert synced.svc.start_subject(subject.id, start)
    uid = synced.svc.active_session.uid
    assert synced.engine.sync().ok
    heartbeat = start + timedelta(minutes=74)
    synced.svc.heartbeat_active_session(heartbeat)

    if repair:
        assert synced.engine.full_resync(SyncResult())
    else:
        # Trigger a fresh server row whose heartbeat is still the original
        # start; the locally recorded heartbeat must remain newer.
        synced.server_db.connection.execute("UPDATE sessions SET duration_seconds = 0 WHERE uid = ?", (uid,))
        synced.server_db.connection.commit()
        assert synced.engine.sync().ok
    local = synced.mirror.get_session(synced.mirror.id_for_uid("sessions", uid))
    assert local.last_active_at == heartbeat.isoformat()
    resumed = SyncedTrackerService(synced.mirror, device_id="macbook")
    assert resumed.active_session.uid == uid
    assert resumed.get_active_recovery_info() is None


def test_foreign_running_heartbeat_remains_server_authoritative(world):
    uid = server_add_subject(world)
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    response = world.http.post("/ops", json={"ops": [{
        "op_id": sync_policy.new_uid(), "op": "start_subject", "device_id": "phone",
        "params": {"subject_uid": uid, "uid": sync_policy.new_uid(), "start_time": start},
    }]})
    assert response.status_code == 200
    session_uid = response.json()["applied"][0]["result"]["session"]["uid"]
    assert world.engine.sync().ok
    future_heartbeat = datetime.now().isoformat()
    world.mirror.connection.execute("UPDATE sessions SET last_active_at = ? WHERE uid = ?", (future_heartbeat, session_uid))
    world.mirror.connection.commit()
    assert world.engine.full_resync(SyncResult())
    local = world.mirror.get_session(world.mirror.id_for_uid("sessions", session_uid))
    assert local.last_active_at == start
