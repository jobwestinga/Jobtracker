"""Weight history, authenticated writes, imports, sync and backup invariants."""
import csv
import io
import uuid
from datetime import datetime

import pytest

from jobtracker.core.database import Database
from jobtracker.services.weight_service import WeightService, validate_measurement
from server.import_weights import load_project, load_scale
from tests.test_api import api, do, result_of  # noqa: F401


def test_daily_weight_save_is_fast_idempotent_update(database):
    service = WeightService(database)
    first = service.upsert("2026-10-10T07:00:00", "80,25", uid=str(uuid.uuid4()))["measurement"]
    second = service.upsert("2026-10-10T08:00:00", 80.5, uid=str(uuid.uuid4()))["measurement"]
    assert len(service.list_measurements()) == 1
    assert second["uid"] == first["uid"]
    assert second["weight_kg"] == 80.5
    assert "id" not in second


@pytest.mark.parametrize("value", [0, -1, 1001, float("nan"), float("inf"), True, False, None, "", "kg", "1,2,3"])
def test_invalid_weight_never_writes(database, value):
    with pytest.raises(ValueError):
        WeightService(database).upsert("2026-10-10T07:00:00", value)
    assert WeightService(database).list_measurements() == []


@pytest.mark.parametrize("value", ["bad", "2026-02-30", "2026-10-10T07:00:00Z", "2026-10-10T07:00:00+02:00", None])
def test_invalid_date_never_writes(database, value):
    with pytest.raises(ValueError):
        WeightService(database).upsert(value, 80)
    assert WeightService(database).list_measurements() == []


def test_weights_use_calendar_days_even_before_logical_boundary(database):
    service = WeightService(database)
    service.upsert("2026-10-09T23:00:00", 80)
    service.upsert("2026-10-10T01:00:00", 81)
    assert len(service.list_measurements()) == 2


def test_import_keeps_scale_multiple_readings_and_historic_identity(database):
    service = WeightService(database)
    rows = [
        {"measured_at": "2026-10-10T07:00:00", "weight_kg": 80, "source": "scale"},
        {"measured_at": "2026-10-10T08:00:00", "weight_kg": 81, "source": "scale"},
        {"measured_at": "2010-01-01T00:00:00", "weight_kg": 60, "source": "historic"},
    ]
    assert service.import_rows(rows)["imported"] == 3
    assert service.import_rows(rows)["unchanged"] == 3
    original = service.list_measurements()
    service.upsert(original[0]["measured_at"], 61, weight_uid=original[0]["uid"])
    assert service.import_rows(rows)["unchanged"] == 3
    assert service.list_measurements()[0]["weight_kg"] == 61


def test_import_is_all_or_nothing(database):
    with pytest.raises(ValueError):
        WeightService(database).import_rows([
            {"measured_at": "2026-10-10T00:00:00", "weight_kg": 80},
            {"measured_at": "invalid", "weight_kg": 81},
        ])
    assert WeightService(database).list_measurements() == []


def test_offline_alias_edit_and_delete_find_existing_daily_row(database):
    service = WeightService(database)
    a = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    offline_uid = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=offline_uid)
    updated = service.upsert("2026-10-10T08:00:00", 82, weight_uid=offline_uid)["measurement"]
    assert updated["uid"] == a["uid"]
    assert updated["weight_kg"] == 82
    assert service.delete(offline_uid)
    assert service.list_measurements() == []
    assert service.upsert("2026-10-10T08:00:00", 83, weight_uid=offline_uid)["skipped"]
    assert not service.delete(offline_uid)


def test_weight_backup_round_trip_preserves_aliases(database, tmp_path):
    service = WeightService(database)
    service.upsert("2026-10-10T07:00:00", 80)
    alias = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    backup = database.export_data()
    restored = Database(tmp_path / "restore.db")
    try:
        restored.import_data(backup)
        restored.import_data(backup)
        assert WeightService(restored).list_measurements() == service.list_measurements()
        assert WeightService(restored).delete(alias)
        assert WeightService(restored).list_measurements() == []
    finally:
        restored.connection.close()


def test_old_backup_keeps_new_weight_history(database):
    service = WeightService(database)
    service.upsert("2026-10-10T07:00:00", 80)
    database.import_data({"subjects": [], "sessions": []})
    assert len(service.list_measurements()) == 1


def test_weight_api_private_and_snapshot_optional(api):
    saved = result_of(do(api, "upsert_weight", {"measured_at": "2026-10-10T07:00:00", "weight_kg": "80,1"}))["measurement"]
    response = api.get("/api/weight")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["measurements"][0]["uid"] == saved["uid"]
    assert "weights" in api.get("/api/snapshot").json()
    assert "weights" not in api.get("/api/snapshot?include_weights=false").json()
    assert api.get("/sync/integrity").json()["tables"]["weights"]["count"] == 1
    assert any(r["table"] == "weights" for r in api.get("/sync/pull?since=0").json()["changes"])
    api.headers.pop("Authorization")
    assert api.get("/api/weight").status_code == 401


def test_repeated_weight_op_and_batch_offline_alias(api):
    original = result_of(do(api, "upsert_weight", {"measured_at": "2026-10-10T07:00:00", "weight_kg": 80}))["measurement"]
    alias = str(uuid.uuid4())
    batch = {"ops": [
        {"op_id": str(uuid.uuid4()), "op": "upsert_weight", "params": {"uid": alias, "measured_at": "2026-10-10T08:00:00", "weight_kg": 81}},
        {"op_id": str(uuid.uuid4()), "op": "upsert_weight", "params": {"weight_uid": alias, "measured_at": "2026-10-10T08:00:00", "weight_kg": 82}},
        {"op_id": str(uuid.uuid4()), "op": "delete_weight", "params": {"weight_uid": alias}},
    ]}
    assert api.post("/ops", json=batch).status_code == 200
    assert api.post("/ops", json=batch).status_code == 200
    assert api.get("/api/weight").json()["measurements"] == []
    assert api.get("/sync/integrity").json()["tables"]["weights"]["count"] == 0


def test_corrupt_import_rolls_back_whole_server_op(api):
    response = do(api, "import_weights", {"measurements": [
        {"measured_at": "2026-10-10T00:00:00", "weight_kg": 80},
        {"measured_at": "bad", "weight_kg": 81},
    ]})
    assert response.status_code == 400
    assert api.get("/api/weight").json()["measurements"] == []


def test_latest_csv_precedence_and_workbook_blanks(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    data = tmp_path / "data"; data.mkdir()
    (data / "scale.CSV").write_text("Preamble\nDate;Time;Weight;Body fat\n10-10-2026;07:00;80.5;20\n10-10-2026;08:00;80.6;20\n")
    for name, rows in [("WeightData.xlsx", [(datetime(2026, 10, 10), 90), (datetime(2026, 10, 9), 81), (None, None)]), ("HistoricWeight.xlsx", [(datetime(2010, 1, 1), 60)])]:
        workbook = openpyxl.Workbook(); sheet = workbook.active
        sheet.append(["Date", "Weight"])
        for row in rows: sheet.append(row)
        workbook.save(data / name); workbook.close()
    rows = load_project(tmp_path)
    assert [r["weight_kg"] for r in rows] == [60, 81, 80.5, 80.6]
    assert [r["source"] for r in rows] == ["historic", "manual", "scale", "scale"]
    assert load_project(tmp_path) == rows


def test_csv_dedup_and_locale_decimal(tmp_path):
    file = tmp_path / "scale.csv"
    file.write_text("Date;Time;Weight\n10-10-2026;07:00;80,5\n10-10-2026;07:00;80,6\n")
    rows = load_scale(file)
    assert len(rows) == 1
    assert rows[0]["weight_kg"] == 80.6


@pytest.mark.parametrize("query", ["start_date=bad", "start_date=2026-10-10&end_date=2026-10-09"])
def test_custom_plot_rejects_bad_bounds(api, query):
    for route in ("breakdown", "agenda"):
        assert api.get(f"/api/graphs/{route}?{query}").status_code == 400


def test_custom_agenda_bounds_limit(api):
    assert api.get("/api/graphs/agenda?start_date=2026-01-01&end_date=2026-10-10").status_code == 400
    assert api.get("/api/graphs/agenda?start_date=2026-10-01&end_date=2026-10-10").status_code == 200


def test_all_time_plot_includes_history_older_than_ten_years(api):
    subject = result_of(do(api, "add_subject", {"name": "Long history"}))["subject"]["uid"]
    do(api, "add_session", {"subject_uid": subject, "start_time": "2010-01-01T10:00:00", "end_time": "2010-01-01T11:00:00"})
    response = api.get("/api/graphs/breakdown?all_time=true&grouping=monthly")
    assert response.status_code == 200
    assert sum(row["total_seconds"] for row in response.json()["buckets"]) == 3600


@pytest.mark.parametrize("created_at", [{"bad": "object"}, ["array"], "invalid", "2026-10-10T10:00:00Z"])
def test_malformed_creation_time_refused_without_partial_import(api, created_at):
    response = do(api, "import_weights", {"measurements": [
        {"measured_at": "2026-10-10T07:00:00", "weight_kg": 80, "created_at": created_at},
    ]})
    assert response.status_code == 400
    assert api.get("/api/weight").json()["measurements"] == []


def test_imported_alias_uid_keeps_existing_edited_measurement(database):
    service = WeightService(database)
    canonical = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    alias = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    result = service.import_rows([{
        "uid": alias, "measured_at": "2026-10-11T09:00:00", "weight_kg": 90, "source": "manual",
    }])
    assert result == {"imported": 0, "unchanged": 1}
    rows = service.list_measurements()
    assert len(rows) == 1 and rows[0]["uid"] == canonical["uid"]
    assert rows[0]["weight_kg"] == 81
    assert service.delete(alias)
    assert service.list_measurements() == []


def test_import_skips_alias_identity_but_still_adds_unrelated_reading(database):
    service = WeightService(database)
    service.upsert("2026-10-10T07:00:00", 80)
    alias = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    result = service.import_rows([
        {"uid": alias, "measured_at": "2026-10-10T09:00:00", "weight_kg": 82},
        {"measured_at": "2026-10-11T09:00:00", "weight_kg": 83},
    ])
    assert result == {"imported": 1, "unchanged": 1}
    assert service.delete(alias)
    assert [row["weight_kg"] for row in service.list_measurements()] == [83]


def test_create_using_an_existing_alias_updates_canonical_row(database):
    service = WeightService(database)
    canonical = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    alias = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    updated = service.upsert("2026-10-11T09:00:00", 82, uid=alias)["measurement"]
    assert updated["uid"] == canonical["uid"]
    assert len(service.list_measurements()) == 1
    assert service.delete(alias)


@pytest.mark.parametrize("invalid_target", [False, "", 0, [], {}])
def test_explicit_invalid_edit_target_does_not_create_measurement(database, invalid_target):
    with pytest.raises(ValueError, match="Invalid measurement identity"):
        WeightService(database).upsert("2026-10-10T07:00:00", 80, weight_uid=invalid_target)
    assert WeightService(database).list_measurements() == []


def test_none_edit_target_is_an_ordinary_daily_save(database):
    saved = WeightService(database).upsert("2026-10-10T07:00:00", 80, weight_uid=None)
    assert saved["measurement"]["weight_kg"] == 80


@pytest.mark.parametrize("use_alias", [False, True])
def test_create_identity_cannot_be_reassigned_to_a_different_edit_target(database, use_alias):
    service = WeightService(database)
    first = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    second = service.upsert("2026-10-11T07:00:00", 81)["measurement"]
    create_uid = first["uid"]
    if use_alias:
        create_uid = str(uuid.uuid4())
        service.upsert("2026-10-10T08:00:00", 82, uid=create_uid)
    before = service.list_measurements()
    with pytest.raises(ValueError, match="different entries"):
        service.upsert("2026-10-11T08:00:00", 90, uid=create_uid, weight_uid=second["uid"])
    assert service.list_measurements() == before
    assert service._resolve(create_uid)["uid"] == first["uid"]


@pytest.mark.parametrize("invalid_uid", [False, "", 0, []])
def test_explicit_invalid_import_identity_is_not_replaced_silently(database, invalid_uid):
    with pytest.raises(ValueError, match="Invalid measurement identity"):
        WeightService(database).import_rows([{
            "uid": invalid_uid, "measured_at": "2026-10-10T07:00:00", "weight_kg": 80,
        }])
    assert WeightService(database).list_measurements() == []


def test_preexisting_alias_collision_is_refused_without_editing_either_entry(database):
    service = WeightService(database)
    service.upsert("2026-10-10T07:00:00", 80)
    alias = str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    # Simulate the former importer bug so an ambiguous identity cannot choose
    # whichever row SQLite happens to return first.
    database.connection.execute(
        "INSERT INTO weight_measurements (uid, measured_at, weight_kg) VALUES (?, ?, ?)",
        (alias, "2026-10-11T07:00:00", 82),
    )
    database.connection.commit()
    before = service.list_measurements()
    with pytest.raises(ValueError, match="conflicting entries"):
        service.upsert("2026-10-10T09:00:00", 90, weight_uid=alias)
    assert service.list_measurements() == before


@pytest.mark.parametrize("day, neighbor", [
    ("9999-12-31", "9999-12-30"),
    ("0001-01-01", "0001-01-02"),
])
def test_daily_save_supports_iso_date_extremes(database, day, neighbor):
    service = WeightService(database)
    service.upsert(neighbor + "T23:59:59", 79)
    first = service.upsert(day, 80)["measurement"]
    alias = str(uuid.uuid4())
    updated = service.upsert(day + "T23:59:59", 81, uid=alias)["measurement"]
    assert updated["uid"] == first["uid"]
    assert updated["measured_at"] == day + "T23:59:59"
    assert len(service.list_measurements()) == 2
    assert service.delete(alias)
    assert [row["weight_kg"] for row in service.list_measurements()] == [79]


def test_backup_alias_cannot_shadow_an_existing_canonical_identity(database):
    service = WeightService(database)
    first = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    incoming_uid = str(uuid.uuid4())
    before = service.list_measurements()
    with pytest.raises(ValueError, match="conflicts with an existing measurement"):
        database.import_data({
            "weights": [{"uid": incoming_uid, "measured_at": "2026-10-11T07:00:00", "weight_kg": 81}],
            "weight_aliases": [{"alias_uid": first["uid"], "weight_uid": incoming_uid}],
        })
    assert service.list_measurements() == before
    assert database.connection.execute("SELECT COUNT(*) FROM weight_uid_aliases").fetchone()[0] == 0
    assert service._resolve(first["uid"])["uid"] == first["uid"]


def test_backup_alias_cannot_remap_an_existing_alias(database):
    service = WeightService(database)
    first = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    alias, incoming_uid = str(uuid.uuid4()), str(uuid.uuid4())
    service.upsert("2026-10-10T08:00:00", 81, uid=alias)
    before = service.list_measurements()
    with pytest.raises(ValueError, match="refers to different entries"):
        database.import_data({
            "weights": [{"uid": incoming_uid, "measured_at": "2026-10-11T07:00:00", "weight_kg": 82}],
            "weight_aliases": [{"alias_uid": alias, "weight_uid": incoming_uid}],
        })
    assert service.list_measurements() == before
    assert service._resolve(alias)["uid"] == first["uid"]


@pytest.mark.parametrize("invalid_uid", [False, "", 1, [], {}])
@pytest.mark.parametrize("field", ["alias_uid", "weight_uid"])
def test_backup_alias_rejects_invalid_identity_without_partial_import(database, invalid_uid, field):
    service = WeightService(database)
    canonical = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    before = service.list_measurements()
    alias = {"alias_uid": str(uuid.uuid4()), "weight_uid": canonical["uid"]}
    alias[field] = invalid_uid
    with pytest.raises(ValueError, match="Invalid weight identity alias"):
        database.import_data({
            "weights": [{"measured_at": "2026-10-11T07:00:00", "weight_kg": 81}],
            "weight_aliases": [alias],
        })
    assert service.list_measurements() == before


def test_backup_redundant_self_alias_is_safe_and_idempotent(database):
    service = WeightService(database)
    canonical = service.upsert("2026-10-10T07:00:00", 80)["measurement"]
    backup = {"weight_aliases": [{"alias_uid": canonical["uid"], "weight_uid": canonical["uid"]}]}
    database.import_data(backup)
    database.import_data(backup)
    assert len(service.list_measurements()) == 1
    assert service._resolve(canonical["uid"])["uid"] == canonical["uid"]


def test_backup_alias_of_absent_measurement_remains_ignored(database):
    database.import_data({"weight_aliases": [{
        "alias_uid": str(uuid.uuid4()), "weight_uid": str(uuid.uuid4()),
    }]})
    assert database.connection.execute("SELECT COUNT(*) FROM weight_uid_aliases").fetchone()[0] == 0
