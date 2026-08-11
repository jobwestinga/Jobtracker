"""Auto-backup on quit: file writing, rotation, and restorability."""

import json
from datetime import datetime, timedelta

from jobtracker.core.auto_backup import (
    BACKUP_PREFIX,
    DEFAULT_KEEP,
    write_auto_backup,
)
from jobtracker.core.database import Database


def test_write_auto_backup_creates_readable_json(tmp_path):
    data = {"subjects": [{"name": "Physics"}], "settings": []}
    path = write_auto_backup(data, tmp_path / "backups")
    assert path.exists()
    assert path.name.startswith(BACKUP_PREFIX)
    assert json.loads(path.read_text(encoding="utf-8")) == data


def test_rotation_keeps_only_newest(tmp_path):
    backups = tmp_path / "backups"
    base = datetime(2026, 7, 1, 12, 0, 0)
    for i in range(DEFAULT_KEEP + 3):
        write_auto_backup({"n": i}, backups, now=base + timedelta(minutes=i))
    files = sorted(backups.glob(f"{BACKUP_PREFIX}*.json"))
    assert len(files) == DEFAULT_KEEP
    # The oldest three runs are gone, the newest survive in order.
    assert json.loads(files[0].read_text())["n"] == 3
    assert json.loads(files[-1].read_text())["n"] == DEFAULT_KEEP + 2


def test_prune_ignores_unrelated_files(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    manual = backups / "my_precious_manual_backup.json"
    manual.write_text("{}")
    for i in range(DEFAULT_KEEP + 2):
        write_auto_backup(
            {"n": i}, backups, now=datetime(2026, 7, 1) + timedelta(minutes=i)
        )
    assert manual.exists()  # only autobackup_* files are ever pruned


def test_backup_round_trips_through_import(tmp_path, database, subject, service):
    service.add_session(
        subject.id,
        datetime(2026, 6, 20, 9, 0),
        datetime(2026, 6, 20, 10, 0),
    )
    path = write_auto_backup(database.export_data(), tmp_path / "backups")

    other = Database(tmp_path / "restored.db")
    try:
        other.import_data(json.loads(path.read_text(encoding="utf-8")))
        restored = other.get_subject_by_name("Physics")
        assert restored is not None
        sessions = other.get_sessions_for_subject(restored.id)
        assert len(sessions) == 1
        assert sessions[0].duration_seconds == 3600
    finally:
        other.connection.close()
