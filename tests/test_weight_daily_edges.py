"""Daily weight entry across offline replay, date boundaries and mirror repair."""

import uuid

import pytest

from jobtracker.core.database import Database
from jobtracker.services.weight_service import WeightService
from jobtracker.sync import state
from jobtracker.sync.client import SyncError
from jobtracker.sync.service import SyncedTrackerService
from tests.test_api import do, result_of
from tests.test_sync_service import synced  # noqa: F401


def measurements(database):
    return WeightService(database).list_measurements()


def server_weight(world, day, value):
    response = do(world.http, "upsert_weight", {"measured_at": day, "weight_kg": value})
    assert response.status_code == 200, response.text
    return result_of(response)["measurement"]


def test_offline_daily_changes_survive_reopening_and_last_value_wins(synced):
    synced.client.offline = True
    first = synced.svc.upsert_weight("2026-10-10", 81.1)["measurement"]
    second = synced.svc.upsert_weight("2026-10-10", 81.2)["measurement"]
    assert first["uid"] == second["uid"]
    assert synced.engine.sync().offline

    # Open a second connection as a new process would. The UI row and queue
    # must both have reached disk, with the original stable identities.
    reopened = Database(synced.mirror.db_path)
    try:
        resumed = SyncedTrackerService(reopened, device_id="macbook", recover=False)
        assert resumed.list_weight_measurements()[0]["weight_kg"] == 81.2
        resumed.upsert_weight("2026-10-10", 81.3)
        queued = state.pending(reopened.connection)
        assert len(queued) == 3
        assert {entry["params"]["uid"] for entry in queued} == {first["uid"]}
    finally:
        reopened.connection.close()

    synced.client.offline = False
    result = synced.engine.sync()
    assert result.ok, result.error
    assert result.pushed == 3
    assert result.pending == 0
    assert measurements(synced.server_db) == measurements(synced.mirror)
    assert len(measurements(synced.mirror)) == 1
    assert measurements(synced.mirror)[0]["weight_kg"] == 81.3
    assert measurements(synced.mirror)[0]["measured_at"] == "2026-10-10T00:00:00"


@pytest.mark.parametrize("delete_after_edit", [False, True])
def test_unknown_remote_same_day_weight_coalesces_then_replays_edit_and_delete(synced, delete_after_edit):
    canonical = server_weight(synced, "2026-10-10", 80.0)
    local = synced.svc.upsert_weight("2026-10-10", 80.1)["measurement"]
    assert local["uid"] != canonical["uid"]
    synced.svc.upsert_weight("2026-10-10", 80.2, weight_uid=local["uid"])
    if delete_after_edit:
        assert synced.svc.delete_weight(local["uid"])

    result = synced.engine.sync()
    assert result.ok, result.error
    assert result.pending == 0
    assert measurements(synced.server_db) == measurements(synced.mirror)
    if delete_after_edit:
        assert measurements(synced.mirror) == []
    else:
        assert len(measurements(synced.mirror)) == 1
        assert measurements(synced.mirror)[0]["uid"] == canonical["uid"]
        assert measurements(synced.mirror)[0]["weight_kg"] == 80.2
        assert result.repaired  # Optimistic uid is replaced by the canonical uid.


def test_canonical_repair_allows_next_native_daily_update(synced):
    canonical = server_weight(synced, "2026-10-10", 80.0)
    synced.svc.upsert_weight("2026-10-10", 80.1)
    assert synced.engine.sync().ok

    saved = synced.svc.upsert_weight("2026-10-10", 80.2)["measurement"]
    assert saved["uid"] == canonical["uid"]
    assert state.pending(synced.mirror.connection)[0]["params"]["uid"] == canonical["uid"]
    result = synced.engine.sync()
    assert result.ok, result.error
    assert not result.repaired
    assert measurements(synced.server_db) == measurements(synced.mirror)
    assert measurements(synced.server_db)[0]["weight_kg"] == 80.2


def test_daily_edit_created_during_canonical_pull_is_preserved_for_next_pass(synced):
    canonical = server_weight(synced, "2026-10-10", 80.0)
    local = synced.svc.upsert_weight("2026-10-10", 80.1)["measurement"]
    real_pull = synced.client.pull

    def edit_while_canonical_is_arriving(*args, **kwargs):
        response = real_pull(*args, **kwargs)
        synced.svc.upsert_weight("2026-10-10", 80.2, weight_uid=local["uid"])
        return response

    synced.client.pull = edit_while_canonical_is_arriving
    first_result = synced.engine.sync()
    assert first_result.ok
    assert first_result.pending == 1
    assert measurements(synced.mirror)[0]["uid"] == local["uid"]
    assert measurements(synced.mirror)[0]["weight_kg"] == 80.2
    assert measurements(synced.server_db)[0]["weight_kg"] == 80.1

    synced.client.pull = real_pull
    final_result = synced.engine.sync()
    assert final_result.ok, final_result.error
    assert final_result.pending == 0
    assert measurements(synced.server_db) == measurements(synced.mirror)
    assert measurements(synced.mirror)[0]["uid"] == canonical["uid"]
    assert measurements(synced.mirror)[0]["weight_kg"] == 80.2


def test_lost_weight_batch_response_keeps_replay_identities_and_final_daily_value(synced):
    server_weight(synced, "2026-10-10", 80.0)
    created = synced.svc.upsert_weight("2026-10-10", 80.1)["measurement"]
    synced.svc.upsert_weight("2026-10-10", 80.2, weight_uid=created["uid"])
    synced.svc.upsert_weight("2026-10-11", 80.3)
    queued_before = state.pending(synced.mirror.connection)
    real_send = synced.client.send_ops

    def lose_response(*args, **kwargs):
        real_send(*args, **kwargs)
        raise SyncError("connection dropped after the server committed")

    synced.client.send_ops = lose_response
    first_result = synced.engine.sync()
    assert first_result.offline
    assert first_result.pending == 3
    assert state.pending(synced.mirror.connection) == queued_before
    assert [row["weight_kg"] for row in measurements(synced.server_db)] == [80.2, 80.3]

    synced.client.send_ops = real_send
    retry = synced.engine.sync()
    assert retry.ok, retry.error
    assert retry.pending == 0
    assert retry.pushed == 3
    assert measurements(synced.mirror) == measurements(synced.server_db)
    assert [row["weight_kg"] for row in measurements(synced.mirror)] == [80.2, 80.3]


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_weight_and_outbox_are_atomic_even_when_enqueue_partly_succeeds(synced, monkeypatch, operation):
    existing = None
    if operation != "create":
        existing = synced.svc.upsert_weight("2026-10-10", 80.0)["measurement"]
        assert synced.engine.sync().ok
    before = measurements(synced.mirror)
    real_enqueue = state.enqueue

    def partly_enqueue_then_fail(*args, **kwargs):
        real_enqueue(*args, **kwargs)
        raise OSError("disk became full before commit")

    monkeypatch.setattr(state, "enqueue", partly_enqueue_then_fail)
    with pytest.raises(OSError, match="disk became full"):
        if operation == "delete":
            synced.svc.delete_weight(existing["uid"])
        elif operation == "update":
            synced.svc.upsert_weight("2026-10-10", 80.2, weight_uid=existing["uid"])
        else:
            synced.svc.upsert_weight("2026-10-10", 80.2)
    assert measurements(synced.mirror) == before
    assert state.pending_count(synced.mirror.connection) == 0
    assert synced.mirror.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("previous_day,next_day", [
    ("2026-10-09", "2026-10-10"),
    ("2026-03-28", "2026-03-29"),  # Amsterdam spring daylight saving change.
    ("2026-10-24", "2026-10-25"),  # Amsterdam autumn daylight saving change.
    ("2028-02-28", "2028-02-29"),
    ("2026-12-31", "2027-01-01"),
])
def test_weight_calendar_midnight_stays_independent_of_custom_tracking_day(synced, previous_day, next_day):
    synced.svc.set_day_start("12:00")
    first = synced.svc.upsert_weight(f"{previous_day}T23:59:59", 80.1)["measurement"]
    second = synced.svc.upsert_weight(next_day, 80.2)["measurement"]
    latest = synced.svc.upsert_weight(f"{next_day}T02:59:59", 80.3)["measurement"]
    assert first["uid"] != second["uid"]
    assert second["uid"] == latest["uid"]
    assert len(synced.svc.list_weight_measurements()) == 2
    result = synced.engine.sync()
    assert result.ok, result.error
    assert result.pending == 0
    assert measurements(synced.mirror) == measurements(synced.server_db)
    assert [row["measured_at"][:10] for row in measurements(synced.server_db)] == [previous_day, next_day]
    assert [row["weight_kg"] for row in measurements(synced.server_db)] == [80.1, 80.3]


def test_edit_of_removed_native_weight_does_not_recreate_or_queue_it(synced):
    original = synced.svc.upsert_weight("2026-10-10", 80.0)["measurement"]
    assert synced.engine.sync().ok
    assert do(synced.http, "delete_weight", {"weight_uid": original["uid"]}).status_code == 200
    assert synced.engine.sync().ok
    outcome = synced.svc.upsert_weight("2026-10-10", 80.1, weight_uid=original["uid"])
    assert outcome["skipped"]
    assert state.pending_count(synced.mirror.connection) == 0
    assert measurements(synced.mirror) == []


def test_native_history_preserves_imported_scale_readings_when_saving_daily_weight(synced):
    imported = [
        {"uid": str(uuid.uuid4()), "measured_at": "2026-10-10T07:10:15", "weight_kg": 80.1, "source": "scale"},
        {"uid": str(uuid.uuid4()), "measured_at": "2026-10-10T09:32:01", "weight_kg": 80.2, "source": "scale"},
    ]
    assert do(synced.http, "import_weights", {"measurements": imported}).status_code == 200
    assert synced.engine.sync().ok
    original_scale = [row for row in measurements(synced.mirror) if row["source"] == "scale"]
    manual = synced.svc.upsert_weight("2026-10-10", 80.3)["measurement"]
    synced.svc.upsert_weight("2026-10-10", 80.4, weight_uid=manual["uid"])
    assert synced.engine.sync().ok
    final = measurements(synced.mirror)
    assert [row for row in final if row["source"] == "scale"] == original_scale
    assert len(final) == 3
    assert len([row for row in final if row["source"] == "manual"]) == 1
    assert measurements(synced.server_db) == final


def daily_pair(weights):
    source = weights.upsert("2026-10-09", 80.0)["measurement"]
    source_alias = str(uuid.uuid4())
    weights.upsert("2026-10-09", 80.1, uid=source_alias)
    destination = weights.upsert("2026-10-10", 81.0)["measurement"]
    destination_alias = str(uuid.uuid4())
    weights.upsert("2026-10-10", 81.1, uid=destination_alias)
    return source, source_alias, destination, destination_alias


def aliases(database):
    return dict(database.connection.execute(
        "SELECT alias_uid, weight_uid FROM weight_uid_aliases"
    ).fetchall())


def test_manual_date_move_merges_destination_and_preserves_both_sides_identities(database):
    weights = WeightService(database)
    source, source_alias, destination, destination_alias = daily_pair(weights)
    saved = weights.upsert("2026-10-10", 82.0, weight_uid=source_alias)["measurement"]
    assert saved["uid"] == destination["uid"]
    assert saved["weight_kg"] == 82.0
    assert saved["measured_at"] == "2026-10-10T00:00:00"
    assert len(weights.list_measurements()) == 1
    assert aliases(database) == {
        source_alias: destination["uid"],
        source["uid"]: destination["uid"],
        destination_alias: destination["uid"],
    }

    # A retry of the same move and later queued edits under any old identity
    # all refer to the same surviving reading.
    for identity in (source_alias, source["uid"], destination_alias, destination["uid"]):
        updated = weights.upsert("2026-10-10", 82.1, weight_uid=identity)["measurement"]
        assert updated["uid"] == destination["uid"]
        assert len(weights.list_measurements()) == 1
    assert weights.delete(source_alias)
    assert weights.list_measurements() == []
    assert aliases(database) == {}
    assert not weights.delete(source["uid"])


def test_manual_move_with_additional_new_uid_follows_merged_destination(database):
    weights = WeightService(database)
    source, _, destination, _ = daily_pair(weights)
    additional = str(uuid.uuid4())
    saved = weights.upsert("2026-10-10", 82.0, uid=additional, weight_uid=source["uid"])["measurement"]
    assert saved["uid"] == destination["uid"]
    assert aliases(database)[additional] == destination["uid"]
    assert weights.delete(additional)
    assert weights.list_measurements() == []


@pytest.mark.parametrize("conflicting_identity", ["destination", "destination_alias", "unrelated"])
def test_conflicting_create_identity_is_checked_before_manual_date_move(database, conflicting_identity):
    weights = WeightService(database)
    source, _, destination, destination_alias = daily_pair(weights)
    third = weights.upsert("2026-10-11", 83.0)["measurement"]
    create_uid = {"destination": destination["uid"], "destination_alias": destination_alias,
                  "unrelated": third["uid"]}[conflicting_identity]
    before, aliases_before = weights.list_measurements(), aliases(database)
    with pytest.raises(ValueError, match="different entries"):
        weights.upsert("2026-10-10", 82.0, uid=create_uid, weight_uid=source["uid"])
    assert weights.list_measurements() == before
    assert aliases(database) == aliases_before


@pytest.mark.parametrize("source", ["scale", "historic"])
def test_imported_date_correction_does_not_merge_with_a_manual_destination(database, source):
    weights = WeightService(database)
    imported_uid = str(uuid.uuid4())
    weights.import_rows([{
        "uid": imported_uid, "source": source, "measured_at": "2026-10-09T07:35:22", "weight_kg": 79.555,
    }])
    destination = weights.upsert("2026-10-10", 81.0)["measurement"]
    moved = weights.upsert("2026-10-10T07:35:22", 79.555, weight_uid=imported_uid)["measurement"]
    assert moved["uid"] == imported_uid
    assert moved["source"] == source
    assert moved["measured_at"] == "2026-10-10T07:35:22"
    assert len(weights.list_measurements()) == 2
    assert weights._resolve(destination["uid"])["weight_kg"] == 81.0
    assert aliases(database) == {}


def test_daily_merge_and_alias_reparenting_rollback_if_outbox_cannot_commit(synced, monkeypatch):
    source = synced.svc.upsert_weight("2026-10-09", 80.0)["measurement"]
    destination = synced.svc.upsert_weight("2026-10-10", 81.0)["measurement"]
    assert synced.engine.sync().ok
    source_alias, destination_alias = str(uuid.uuid4()), str(uuid.uuid4())
    local = WeightService(synced.mirror)
    local.upsert("2026-10-09", 80.0, uid=source_alias)
    local.upsert("2026-10-10", 81.0, uid=destination_alias)
    before, aliases_before = measurements(synced.mirror), aliases(synced.mirror)

    def refuse_enqueue(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state, "enqueue", refuse_enqueue)
    with pytest.raises(OSError, match="disk full"):
        synced.svc.upsert_weight("2026-10-10", 82.0, weight_uid=source["uid"])
    assert measurements(synced.mirror) == before
    assert aliases(synced.mirror) == aliases_before
    assert state.pending_count(synced.mirror.connection) == 0
    assert local._resolve(source_alias)["uid"] == source["uid"]
    assert local._resolve(destination_alias)["uid"] == destination["uid"]


@pytest.mark.parametrize("known_destination", [False, True])
@pytest.mark.parametrize("delete_after", [False, True])
def test_offline_manual_date_move_converges_via_feed_and_preserves_queued_source_identity(synced, known_destination, delete_after):
    source = server_weight(synced, "2026-10-09", 80.0)
    assert synced.engine.sync().ok
    destination = server_weight(synced, "2026-10-10", 81.0)
    if known_destination:
        assert synced.engine.sync().ok
    synced.client.offline = True
    synced.svc.upsert_weight("2026-10-10", 82.0, weight_uid=source["uid"])
    synced.svc.upsert_weight("2026-10-10", 82.1, weight_uid=source["uid"])
    if delete_after:
        assert synced.svc.delete_weight(source["uid"])
    queued = state.pending(synced.mirror.connection)
    assert {entry["params"]["weight_uid"] for entry in queued} == {source["uid"]}
    synced.client.offline = False
    result = synced.engine.sync()
    assert result.ok, result.error
    assert result.pending == 0
    assert not result.repaired
    assert measurements(synced.mirror) == measurements(synced.server_db)
    if delete_after:
        assert measurements(synced.server_db) == []
    else:
        saved = measurements(synced.server_db)
        assert len(saved) == 1
        assert saved[0]["uid"] == destination["uid"]
        assert saved[0]["weight_kg"] == 82.1
        changes = synced.http.get("/sync/pull?since=0").json()["changes"]
        assert any(row["table"] == "weights" and row["uid"] == source["uid"] and row["op"] == "delete" for row in changes)


def test_manual_date_merge_survives_lost_response_and_backup_alias_round_trip(synced, tmp_path):
    source = server_weight(synced, "2026-10-09", 80.0)
    destination = server_weight(synced, "2026-10-10", 81.0)
    assert synced.engine.sync().ok
    synced.svc.upsert_weight("2026-10-10", 82.0, weight_uid=source["uid"])
    pending_before = state.pending(synced.mirror.connection)
    real_send = synced.client.send_ops

    def lose_response(*args, **kwargs):
        real_send(*args, **kwargs)
        raise SyncError("lost merge acknowledgement")

    synced.client.send_ops = lose_response
    assert synced.engine.sync().offline
    assert state.pending(synced.mirror.connection) == pending_before
    synced.client.send_ops = real_send
    retry = synced.engine.sync()
    assert retry.ok, retry.error
    assert retry.pending == 0
    assert len(measurements(synced.server_db)) == 1
    assert measurements(synced.mirror) == measurements(synced.server_db)

    restored = Database(tmp_path / "merged-backup.db")
    try:
        restored.import_data(synced.server_db.export_data())
        restored_weights = WeightService(restored)
        assert restored_weights._resolve(source["uid"])["uid"] == destination["uid"]
        updated = restored_weights.upsert("2026-10-10", 82.2, weight_uid=source["uid"])["measurement"]
        assert updated["uid"] == destination["uid"]
        assert restored_weights.delete(source["uid"])
        assert restored_weights.list_measurements() == []
    finally:
        restored.connection.close()
