"""Export bundle CSV builders + zip round trip + JSON restore still works."""

import csv
import io
import json
import zipfile
from datetime import datetime

from jobtracker.core import export_bundle
from jobtracker.core.database import Database


def _seed(service):
    s = service.add_subject("Physics", "#3B82F6", "notes")
    service.add_session(s.id, datetime(2026, 6, 20, 4, 0), datetime(2026, 6, 20, 5, 0))
    # A late-night session that belongs to the previous logical day (03:00 start).
    service.add_session(s.id, datetime(2026, 6, 21, 1, 0), datetime(2026, 6, 21, 2, 0))
    return s


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_sessions_csv_has_header_and_rows(service):
    _seed(service)
    text = export_bundle.sessions_csv(service.export_data())
    rows = _rows(text)
    assert rows[0] == ["subject", "start_time", "end_time", "duration_seconds", "duration_hms"]
    assert len(rows) == 3  # header + 2 sessions
    assert rows[1][0] == "Physics"


def test_subjects_csv(service):
    _seed(service)
    rows = _rows(export_bundle.subjects_csv(service.export_data()))
    assert rows[0] == ["name", "color", "archived", "notes", "created_at"]
    assert rows[1][0] == "Physics"
    assert rows[1][1] == "#3B82F6"


def test_daily_summary_respects_03_00_boundary(service):
    _seed(service)
    # Late-night 01:00-02:00 session belongs to the previous logical day (06-20).
    from datetime import time
    breakdown = service.get_subject_breakdown(grouping="daily", days=None, day_start=time(3, 0))
    text = export_bundle.daily_summary_csv(breakdown)
    rows = _rows(text)
    assert rows[0] == ["date", "subject", "hours", "seconds"]
    data = {(r[0], r[1]): r for r in rows[1:]}
    # Both sessions land on 2026-06-20 (04:00 and the after-midnight 01:00 -> prev day).
    assert ("2026-06-20", "Physics") in data
    assert data[("2026-06-20", "Physics")][3] == "7200"  # 2h total
    assert ("2026-06-21", "Physics") not in data


def test_build_bundle_files_keys(service):
    _seed(service)
    breakdown = service.get_subject_breakdown(grouping="daily", days=None)
    files = export_bundle.build_bundle_files(service.export_data(), breakdown)
    assert set(files.keys()) == {
        "jobtracker_backup.json", "sessions.csv", "subjects.csv",
        "daily_summary.csv", "README.txt",
    }


def test_write_zip_and_json_round_trip(service, tmp_path):
    _seed(service)
    breakdown = service.get_subject_breakdown(grouping="daily", days=None)
    zip_path = tmp_path / "bundle.zip"
    export_bundle.write_zip(zip_path, service.export_data(), breakdown)

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "jobtracker_backup.json" in names
        payload = json.loads(zf.read("jobtracker_backup.json"))

    # The JSON inside the bundle must still restore cleanly.
    restore = Database(tmp_path / "restore.db")
    restore.import_data(payload)
    subjects = restore.get_all_subjects_including_archived()
    assert len(subjects) == 1
    assert len(restore.get_sessions_for_subject(subjects[0].id)) == 2
    restore.connection.close()
