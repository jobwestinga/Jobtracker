"""Legacy backup restore must survive retries and authoritative mirror repair."""

from copy import deepcopy

import pytest

pytest.importorskip("fastapi.testclient", reason="server extras not installed")

from jobtracker.core import sync_policy
from jobtracker.sync import state
from jobtracker.sync.engine import SyncResult
from tests.support.synced import synced, table_counts, uid_fingerprint


@pytest.fixture
def legacy_backup():
    weight_uid, alias_uid = sync_policy.new_uid(), sync_policy.new_uid()
    return {
        "tasks": [{"id": 101, "name": "Restored subject", "color": "#123456", "notes": "old notes"}],
        "sessions": [{"id": 201, "task_id": 101, "start_time": "2026-06-01T10:00:00", "end_time": "2026-06-01T11:15:00", "duration_seconds": 4500}],
        "goal_templates": [{"id": 501, "title": "Restored template", "recurrence": "daily", "notes": "template notes"}],
        "todo_tasks": [{"id": 301, "name": "Restored goal", "notes": "goal notes", "template_id": 501, "created_at": "2026-06-01T09:00:00", "sort_order": 4}],
        "milestones": [{"id": 401, "goal_id": 301, "title": "Restored milestone", "sort_order": 0}],
        "weights": [{"uid": weight_uid, "measured_at": "2026-06-01T08:00:00", "weight_kg": 80.25, "source": "scale", "created_at": "2026-06-01T08:00:00"}],
        "weight_aliases": [{"alias_uid": alias_uid, "weight_uid": weight_uid}],
        "settings": [
            {"key": "day_start_time", "value": "05:00"},
            {"key": "theme", "value": "restored-theme"},
            {"key": "server_url", "value": "https://private-device.example"},
            {"key": "device_id", "value": "old-install"},
        ],
    }


def aliases(db):
    return [dict(row) for row in db.connection.execute("SELECT * FROM weight_uid_aliases ORDER BY alias_uid")]


def test_legacy_restore_flush_retry_pull_and_resync_preserve_all_rows(synced, legacy_backup):
    # Existing rows and unrelated historical integer IDs exercise remapping.
    synced.svc.add_subject("Already synced", "#987654", "")
    assert synced.engine.sync().ok
    device_id = synced.mirror.get_setting("device_id")
    synced.svc.import_data(legacy_backup)
    entries = state.pending(synced.mirror.connection)
    assert [entry["op"] for entry in entries] == ["import_backup"]
    normalized = entries[0]["params"]["data"]
    assert all(row.get("uid") for key in ("subjects", "sessions", "goal_templates", "todo_tasks", "milestones", "weights") for row in normalized[key])
    assert normalized["settings"] == [{"key": "day_start_time", "value": "05:00"}]
    assert synced.mirror.get_setting("theme") == "restored-theme"
    assert synced.mirror.get_setting("device_id") == device_id
    identities = uid_fingerprint(synced.mirror)

    # Apply successfully but lose the acknowledgment. Retrying the exact queued
    # normalized snapshot must never mint a second set of legacy identities.
    first = synced.client.send_ops(entries, device_id="macbook")
    assert first["applied"][0]["result"]["replayed"] is False
    first_counts = table_counts(synced.server_db)
    result = synced.engine.sync()
    assert result.ok and state.pending_count(synced.mirror.connection) == 0
    assert table_counts(synced.server_db) == first_counts == table_counts(synced.mirror)
    assert uid_fingerprint(synced.server_db) == identities == uid_fingerprint(synced.mirror)
    assert aliases(synced.server_db) == legacy_backup["weight_aliases"]
    assert synced.server_db.get_setting("theme") != "restored-theme"
    assert synced.server_db.get_setting("server_url") != "https://private-device.example"
    assert synced.server_db.get_setting("day_start_time") == "05:00"

    assert synced.engine.full_resync(SyncResult())
    assert uid_fingerprint(synced.mirror) == identities
    assert aliases(synced.server_db) == legacy_backup["weight_aliases"]
    session = synced.mirror.connection.execute("SELECT duration_seconds FROM sessions").fetchone()
    assert session["duration_seconds"] == 4500
    goal = synced.mirror.connection.execute("SELECT * FROM todo_tasks").fetchone()
    assert synced.mirror.uid_for_id("goal_templates", goal["template_id"]) in identities["goal_templates"]
    milestone = synced.mirror.connection.execute("SELECT * FROM milestones").fetchone()
    assert synced.mirror.uid_for_id("todo_tasks", milestone["goal_id"]) == goal["uid"]
    assert synced.mirror.get_setting("theme") == "restored-theme"


def test_restore_queue_failure_rolls_back_rows_aliases_and_preferences(synced, legacy_backup, monkeypatch):
    before = synced.svc.export_data()
    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")
    monkeypatch.setattr(state, "enqueue", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        synced.svc.import_data(legacy_backup)
    assert synced.svc.export_data() == before
    assert state.pending_count(synced.mirror.connection) == 0


def test_stale_restore_preserves_known_server_identities_and_maps_new_children(synced, legacy_backup):
    synced.svc.import_data(legacy_backup)
    assert synced.engine.sync().ok
    stale = deepcopy(synced.svc.export_data())
    original_ids = uid_fingerprint(synced.server_db)
    # Another device changes every row before this machine restores its stale
    # backup. The normalized mirror export must preserve these server edits.
    with synced.server_db.atomic():
        for table, column in (("tasks", "name"), ("goal_templates", "title"), ("todo_tasks", "name"), ("milestones", "title")):
            synced.server_db.connection.execute(f"UPDATE {table} SET {column} = ?", (f"New server {table}",))
        synced.server_db.connection.execute("UPDATE sessions SET end_time = '2026-06-01T11:30:00', duration_seconds = 5400")
        synced.server_db.connection.execute("UPDATE weight_measurements SET weight_kg = 79.5")
    canonical = {
        table: dict(synced.server_db.connection.execute(f"SELECT * FROM {table}").fetchone())
        for table in sync_policy.SYNCED_TABLES
    }
    old_subject_id = stale["subjects"][0]["id"]
    old_goal_id = stale["todo_tasks"][0]["id"]
    old_template_id = stale["goal_templates"][0]["id"]
    stale["sessions"].append({"task_id": old_subject_id, "start_time": "2026-06-02T10:00:00", "end_time": "2026-06-02T11:00:00", "duration_seconds": 3600})
    stale["milestones"].append({"goal_id": old_goal_id, "title": "New restored milestone", "sort_order": 5})
    stale["todo_tasks"].append({"id": 999, "name": "New restored goal", "template_id": old_template_id})
    synced.svc.import_data(stale)
    assert synced.engine.sync().ok
    for table, old in canonical.items():
        assert dict(synced.server_db.connection.execute(f"SELECT * FROM {table} WHERE uid = ?", (old["uid"],)).fetchone()) == old
    expected = {table: len(uids) for table, uids in original_ids.items()}
    for table in ("sessions", "milestones", "todo_tasks"):
        expected[table] += 1
    assert table_counts(synced.server_db) == expected == table_counts(synced.mirror)
    new_goal = synced.server_db.connection.execute("SELECT * FROM todo_tasks WHERE name = 'New restored goal'").fetchone()
    assert new_goal["template_id"] == canonical["goal_templates"]["id"]
    new_milestone = synced.server_db.connection.execute("SELECT * FROM milestones WHERE title = 'New restored milestone'").fetchone()
    assert new_milestone["goal_id"] == canonical["todo_tasks"]["id"]


@pytest.mark.parametrize("data", [None, [], {"subjects": {}}, {"sessions": [None]}, {"weights": [42]}, {"settings": "bad"}, {"tasks": [{}] * 100_001}])
def test_malformed_backup_is_rejected_without_rows_or_receipt(synced, data):
    before = table_counts(synced.server_db)
    op_id = sync_policy.new_uid()
    response = synced.http.post("/ops", json={"ops": [{"op_id": op_id, "op": "import_backup", "params": {"data": data}}]})
    assert response.status_code == 400, response.text
    assert table_counts(synced.server_db) == before
    assert synced.server_db.connection.execute("SELECT 1 FROM applied_ops WHERE op_id = ?", (op_id,)).fetchone() is None
    with pytest.raises(ValueError):
        synced.svc.import_data(data)
    assert state.pending_count(synced.mirror.connection) == 0


def test_server_backup_handler_filters_device_preferences(synced, legacy_backup):
    request = {"op_id": sync_policy.new_uid(), "op": "import_backup", "params": {"data": legacy_backup}}
    response = synced.http.post("/ops", json={"ops": [request]})
    assert response.status_code == 200, response.text
    assert synced.server_db.get_setting("day_start_time") == "05:00"
    assert synced.server_db.get_setting("theme") != "restored-theme"
    assert synced.server_db.get_setting("server_url") != "https://private-device.example"
