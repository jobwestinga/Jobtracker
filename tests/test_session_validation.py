"""Short edits refuse safely before durable history or sync identity changes."""

from datetime import datetime, timedelta
import uuid

import pytest

from jobtracker.services.session_service import MIN_SESSION_SECONDS
from jobtracker.sync import state
from server import app as app_module
from tests.support.api import api, do, make_subject, result_of
from tests.support.synced import synced


def stored_session(database, session_id):
    row = database.connection.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


@pytest.mark.parametrize("seconds", [0, 1, 20, 29, 29.999999])
def test_short_edit_preserves_existing_row_and_exact_endpoints(service, subject, seconds):
    start = datetime(2026, 10, 10, 9, 0, 0, 900123)
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))
    before = stored_session(service.db, session.id)
    changes = service.db.connection.total_changes

    assert service.update_session(
        session.id, subject.id, start, start + timedelta(seconds=seconds)
    ) is None

    assert stored_session(service.db, session.id) == before
    assert service.db.connection.total_changes == changes


def test_exact_minimum_edit_is_allowed_without_changing_uid(service, subject):
    start = datetime(2026, 10, 10, 9, 0, 0, 900123)
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))

    edited = service.update_session(
        session.id, subject.id, start, start + timedelta(seconds=MIN_SESSION_SECONDS)
    )

    assert edited.id == session.id and edited.uid == session.uid
    assert edited.duration_seconds == MIN_SESSION_SECONDS
    assert edited.start_time == start.isoformat()
    assert edited.end_time == (start + timedelta(seconds=MIN_SESSION_SECONDS)).isoformat()


@pytest.mark.parametrize("seconds", [0, 20, 29.999999])
def test_rejected_synced_short_edit_neither_deletes_nor_queues(synced, seconds):
    subject = synced.svc.add_subject("Review", "#3B82F6", "")
    start = datetime(2026, 10, 10, 9, 0, 0, 900123)
    session = synced.svc.add_session(subject.id, start, start + timedelta(minutes=90))
    assert synced.engine.sync().ok
    assert state.pending_count(synced.mirror.connection) == 0
    before = stored_session(synced.mirror, session.id)

    assert synced.svc.update_session(
        session.id, subject.id, start, start + timedelta(seconds=seconds)
    ) is None

    assert state.pending_count(synced.mirror.connection) == 0
    assert stored_session(synced.mirror, session.id) == before
    assert synced.engine.sync().ok
    server_row = synced.server_db.connection.execute(
        "SELECT * FROM sessions WHERE uid = ?", (session.uid,)
    ).fetchone()
    assert server_row["start_time"] == before["start_time"]
    assert server_row["end_time"] == before["end_time"]
    assert server_row["duration_seconds"] == before["duration_seconds"]


def test_rejected_api_short_edit_leaves_history_feed_and_receipts_unchanged(api):
    subject_uid = make_subject(api)
    start = datetime(2026, 10, 10, 9, 0, 0, 900123)
    session = result_of(do(api, "add_session", {
        "subject_uid": subject_uid,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=90)).isoformat(),
        "uid": str(uuid.uuid4()),
    }))["session"]
    database = app_module._state["db"]
    row_id = database.id_for_uid("sessions", session["uid"])
    before = stored_session(database, row_id)
    head = api.get("/api/context").json()["head"]
    receipts = database.connection.execute("SELECT COUNT(*) FROM applied_ops").fetchone()[0]
    op_id = str(uuid.uuid4())
    params = {
        "session_uid": session["uid"], "subject_uid": subject_uid,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(seconds=20)).isoformat(),
    }

    for _ in range(2):
        response = do(api, "update_session", params, op_id=op_id)
        assert response.status_code == 400
        assert response.json()["failed_op_id"] == op_id
        assert response.json()["failed_index"] == 0
        assert response.json()["applied"] == []
        assert stored_session(database, row_id) == before
        assert api.get("/api/context").json()["head"] == head
        assert database.connection.execute("SELECT COUNT(*) FROM applied_ops").fetchone()[0] == receipts

    # A rejected request never claims its ID. Correcting it can commit once.
    params["end_time"] = (start + timedelta(seconds=MIN_SESSION_SECONDS)).isoformat()
    response = do(api, "update_session", params, op_id=op_id)
    assert response.status_code == 200
    edited = result_of(response)["session"]
    assert edited["uid"] == session["uid"]
    assert edited["duration_seconds"] == MIN_SESSION_SECONDS
    assert result_of(do(api, "update_session", params, op_id=op_id))["replayed"] is True


def test_recovery_retained_short_row_survives_a_refused_day_move(service, subject):
    start = datetime(2026, 10, 10, 9)
    session = service.db.start_session(subject.id, start_time=start)
    service.db.close_recovered_session(session.id, start + timedelta(seconds=10), 10)
    before = stored_session(service.db, session.id)

    assert service.shift_session(session.id, 86400) is None

    assert stored_session(service.db, session.id) == before
