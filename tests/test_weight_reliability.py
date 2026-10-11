"""Offline weight identity and daily-entry regressions."""

import uuid

from jobtracker.services.weight_service import WeightService


def test_offline_edit_follows_daily_coalescing_alias(database):
    weights = WeightService(database)
    canonical = str(uuid.uuid4())
    offline = str(uuid.uuid4())
    weights.upsert("2026-10-10T07:00:00", 80, uid=canonical)
    first = weights.upsert("2026-10-10T08:00:00", 81, uid=offline)
    assert first["measurement"]["uid"] == canonical
    edited = weights.upsert("2026-10-10T08:00:00", 82, weight_uid=offline)
    assert edited["measurement"]["uid"] == canonical
    assert edited["measurement"]["weight_kg"] == 82
    assert len(weights.list_measurements()) == 1


def test_offline_delete_follows_daily_coalescing_alias(database):
    weights = WeightService(database)
    canonical, offline = str(uuid.uuid4()), str(uuid.uuid4())
    weights.upsert("2026-10-10T07:00:00", 80, uid=canonical)
    weights.upsert("2026-10-10T08:00:00", 81, uid=offline)
    assert weights.delete(offline)
    assert weights.list_measurements() == []


def test_alias_survives_new_service_instance(database):
    weights = WeightService(database)
    canonical, offline = str(uuid.uuid4()), str(uuid.uuid4())
    weights.upsert("2026-10-10T07:00:00", 80, uid=canonical)
    weights.upsert("2026-10-10T08:00:00", 81, uid=offline)
    resumed = WeightService(database)
    edited = resumed.upsert("2026-10-10T08:00:00", 82, weight_uid=offline)
    assert edited["measurement"]["weight_kg"] == 82
    assert resumed.delete(offline)


def test_editing_imported_scale_weight_keeps_source_and_exact_timestamp(database):
    weights = WeightService(database)
    uid = str(uuid.uuid4())
    timestamp = "2026-10-10T07:35:00"
    weights.import_rows([{
        "uid": uid, "measured_at": timestamp, "weight_kg": 80, "source": "scale",
    }])
    edited = weights.upsert(timestamp, "79,5", weight_uid=uid)
    assert edited["measurement"]["measured_at"] == timestamp
    assert edited["measurement"]["source"] == "scale"
    assert edited["measurement"]["weight_kg"] == 79.5


def test_reimport_does_not_overwrite_a_weight_edit(database):
    weights = WeightService(database)
    uid = str(uuid.uuid4())
    row = {"uid": uid, "measured_at": "2026-10-10T07:35:00", "weight_kg": 80, "source": "scale"}
    weights.import_rows([row])
    weights.upsert(row["measured_at"], 79.5, weight_uid=uid)
    repeated = weights.import_rows([row])
    assert repeated["imported"] == 0
    assert weights.list_measurements()[0]["weight_kg"] == 79.5

