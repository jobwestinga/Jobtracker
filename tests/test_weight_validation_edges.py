"""Precise imported times, stable identities and chart-boundary regressions."""

from datetime import datetime
import uuid

import pytest

from jobtracker.core.database import Database
from jobtracker.core.weight_plot import points_for
from jobtracker.services.weight_service import WeightService
from server.import_weights import _record
from tests.support.api import api, do, result_of  # noqa: F401


@pytest.mark.parametrize("source", ["manual", "scale", "historic"])
def test_fractional_import_and_unchanged_edit_preserve_timestamp(database, source):
    weights = WeightService(database)
    timestamp = "2026-10-10T07:35:22.123456"
    identity = str(uuid.uuid4())
    row = {"uid": identity, "measured_at": timestamp, "weight_kg": 83.960123, "source": source}

    assert weights.import_rows([row])["imported"] == 1
    edited = weights.upsert(timestamp, row["weight_kg"], weight_uid=identity)["measurement"]
    assert edited["measured_at"] == timestamp
    assert edited["weight_kg"] == row["weight_kg"]
    assert edited["source"] == source
    assert weights.import_rows([row])["unchanged"] == 1
    assert weights.list_measurements() == [edited]


def test_fractional_import_retains_legacy_deterministic_identity(database):
    weights = WeightService(database)
    moment = datetime(2026, 10, 10, 7, 35, 22, 123456)
    legacy_uid = str(uuid.uuid5(
        uuid.NAMESPACE_URL, "jobtracker:weight:scale:2026-10-10T07:35:22"
    ))
    row = _record(moment, 80, "scale")
    assert row["uid"] == legacy_uid
    assert row["measured_at"] == moment.isoformat()
    implicit = {key: value for key, value in row.items() if key != "uid"}
    assert weights.import_rows([implicit])["imported"] == 1
    assert weights.list_measurements()[0]["uid"] == legacy_uid
    assert weights.import_rows([row])["unchanged"] == 1


def test_fractional_reimport_does_not_duplicate_or_overwrite_an_existing_legacy_edit(database):
    weights = WeightService(database)
    row = _record(datetime(2026, 10, 10, 7, 35, 22, 123456), 80, "scale")
    weights.import_rows([{**row, "measured_at": "2026-10-10T07:35:22"}])
    weights.upsert("2026-10-10T08:00:00", 79.55, weight_uid=row["uid"])
    before = weights.list_measurements()

    assert weights.import_rows([row])["unchanged"] == 1
    assert weights.import_rows([{key: value for key, value in row.items() if key != "uid"}])["unchanged"] == 1
    assert weights.list_measurements() == before


def test_explicit_scale_identities_keep_both_readings_within_one_second(database):
    weights = WeightService(database)
    rows = [{"uid": str(uuid.uuid4()), "measured_at": f"2026-10-10T07:35:22.{fraction}",
             "weight_kg": 80, "source": "scale"} for fraction in ["123456", "654321"]]

    assert weights.import_rows(rows)["imported"] == 2
    assert weights.import_rows(rows)["unchanged"] == 2
    assert [row["measured_at"] for row in weights.list_measurements()] == [row["measured_at"] for row in rows]


def test_fractional_backup_round_trip_keeps_aliases_and_distinct_explicit_readings(database, tmp_path):
    weights = WeightService(database)
    timestamp = "2026-10-10T07:35:22.123456"
    first = weights.upsert(timestamp, 80)["measurement"]
    alias = str(uuid.uuid4())
    weights.upsert(timestamp, 81, uid=alias)
    weights.import_rows([{
        "uid": str(uuid.uuid4()), "measured_at": "2026-10-10T07:35:22.654321",
        "weight_kg": 82, "source": "scale",
    }])
    restored = Database(tmp_path / "restored.db")
    try:
        restored.import_data(database.export_data())
        restored.import_data(database.export_data())
        restored_weights = WeightService(restored)
        assert restored_weights.list_measurements() == weights.list_measurements()
        assert restored_weights.list_aliases() == weights.list_aliases()
        assert restored_weights.upsert(timestamp, 83, weight_uid=alias)["measurement"]["uid"] == first["uid"]
        assert restored_weights.delete(alias)
        assert restored_weights.list_measurements()[0]["measured_at"].endswith(".654321")
    finally:
        restored.connection.close()


def test_weight_read_supplies_aliases_after_create_acknowledgement(api):
    first = result_of(do(api, "upsert_weight", {"measured_at": "2026-10-10", "weight_kg": 80}))["measurement"]
    alias = str(uuid.uuid4())
    result_of(do(api, "upsert_weight", {"uid": alias, "measured_at": "2026-10-10", "weight_kg": 81}))
    response = api.get("/api/weight")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["aliases"] == [{"alias_uid": alias, "weight_uid": first["uid"]}]
    assert len(response.json()["measurements"]) == 1
    assert result_of(do(api, "upsert_weight", {"weight_uid": alias, "measured_at": "2026-10-10", "weight_kg": 82}))["measurement"]["uid"] == first["uid"]
    do(api, "delete_weight", {"weight_uid": alias})
    assert api.get("/api/weight").json()["aliases"] == []
    api.headers.pop("Authorization")
    assert api.get("/api/weight").status_code == 401


@pytest.mark.parametrize("value", [True, False, 1001, float("nan"), float("inf"), 0, -1])
def test_native_chart_omits_values_refused_by_weight_validation(value):
    assert points_for([{"measured_at": "2026-10-10", "weight_kg": value}]) == []


def test_native_edit_keeps_imported_fractional_time_and_displays_original_precision(service):
    from PySide6.QtWidgets import QApplication
    from jobtracker.ui.widgets.weight_view import WeightView

    app = QApplication.instance() or QApplication([])
    row = {"uid": str(uuid.uuid4()), "measured_at": "2026-10-10T07:35:22.123456",
           "weight_kg": 83.960123, "source": "scale"}
    WeightService(service.db).import_rows([row])
    widget = WeightView(service)
    try:
        widget.edit_entry(row["uid"])
        assert widget._preview.text() == "83.960123 kg"
        widget._save()
        saved = service.list_weight_measurements()[0]
        assert saved["uid"] == row["uid"]
        assert saved["measured_at"] == row["measured_at"]
        assert saved["weight_kg"] == row["weight_kg"]
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()
