"""An export must represent one database state while sync writes concurrently."""
from datetime import datetime, timedelta
import sqlite3

import pytest

from jobtracker.core.database import Database


def test_export_keeps_related_rows_consistent_across_a_concurrent_delete(database):
    subject = database.add_subject("Exported together", "#123456", "")
    start = datetime(2026, 10, 11, 9)
    session = database.add_session(subject.id, start, start + timedelta(minutes=75), 4500)
    peer = Database(database.db_path)
    deleted = False

    def concurrent_write(sql):
        nonlocal deleted
        if not deleted and sql.upper().startswith("SELECT * FROM SESSIONS"):
            deleted = True
            peer.delete_subject(subject.id)

    database.connection.set_trace_callback(concurrent_write)
    try:
        exported = database.export_data()
    finally:
        database.connection.set_trace_callback(None)
        peer.connection.close()

    assert deleted
    assert [row["uid"] for row in exported["subjects"]] == [subject.uid]
    assert [row["uid"] for row in exported["sessions"]] == [session.uid]
    assert exported["sessions"][0]["task_id"] == exported["subjects"][0]["id"]
    assert database.get_all_subjects() == []
    assert not database.connection.in_transaction


def test_export_does_not_commit_or_rollback_a_callers_pending_transaction(database):
    database.connection.execute("BEGIN")
    database.connection.execute("INSERT INTO settings(key,value) VALUES ('export-probe','pending')")
    exported = database.export_data()
    assert any(row["key"] == "export-probe" for row in exported["settings"])
    assert database.connection.in_transaction
    database.connection.rollback()
    assert database.get_setting("export-probe", "absent") == "absent"


def test_failed_export_releases_its_snapshot_without_losing_existing_rows(database):
    subject = database.add_subject("Still here", "#123456", "")
    database.connection.execute("DROP TABLE milestones")
    database.connection.commit()
    with pytest.raises(sqlite3.OperationalError, match="milestones"):
        database.export_data()
    assert not database.connection.in_transaction
    assert database.get_subject(subject.id).uid == subject.uid
    assert database.add_subject("Can still write", "#654321", "") is not None
