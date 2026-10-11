"""Regression coverage for interrupted timers, delayed replay and stale state."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from jobtracker.core import timeutils
from jobtracker.services.tracker_service import TrackerService


def test_captured_75_minute_session_survives_restart_and_stop(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    assert service.start_subject(subject.id, start_time=start)
    uid = service.active_session.uid
    recovered = TrackerService(database)
    assert recovered.active_session.uid == uid
    recovered.stop_active_subject(start + timedelta(minutes=75))
    recorded = database.get_sessions_for_subject(subject.id)
    assert len(recorded) == 1
    assert recorded[0].start_time == start.isoformat()
    assert recorded[0].duration_seconds == 4500


def test_another_service_cannot_start_a_second_timer(database, service, subject):
    other = TrackerService(database, recover=False)
    assert service.start_subject(subject.id)
    assert not other.start_subject(subject.id)
    assert len(database.get_open_sessions()) == 1


def test_stale_stop_cannot_overwrite_an_already_closed_session(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    service.start_subject(subject.id, start)
    session_id = service.active_session.id
    other = TrackerService(database, recover=False)
    other.stop_active_subject(start + timedelta(minutes=75))
    service.stop_active_subject(start + timedelta(hours=3))
    assert database.get_session(session_id).duration_seconds == 4500
    assert service.active_session is None


def test_stop_resolves_uid_after_local_integer_ids_change(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    service.start_subject(subject.id, start)
    original_id = service.active_session.id
    uid = service.active_session.uid
    database.connection.execute("UPDATE sessions SET id = ? WHERE id = ?", (101, original_id))
    database.connection.execute(
        "INSERT INTO sessions (id, task_id, start_time, device_id) VALUES (?, ?, ?, ?)",
        (original_id, subject.id, (start + timedelta(hours=2)).isoformat(), "phone"),
    )
    database.connection.commit()
    service.stop_active_subject(start + timedelta(minutes=75))
    assert database.get_session(101).uid == uid
    assert database.get_session(101).duration_seconds == 4500
    assert database.get_session(original_id).end_time is None


def test_invalid_earlier_stop_preserves_unfinished_work(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    service.start_subject(subject.id, start)
    session_id = service.active_session.id
    service.stop_active_subject(start - timedelta(minutes=75))
    assert database.get_session(session_id).end_time is None
    assert service.active_session.id == session_id


def test_invalid_session_edit_does_not_delete_the_original(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    session = service.add_session(subject.id, start, start + timedelta(minutes=75))
    assert service.update_session(session.id, subject.id, start, start - timedelta(minutes=1)) is None
    assert database.get_session(session.id).duration_seconds == 4500


def test_editor_cannot_close_an_open_timer(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    service.start_subject(subject.id, start)
    session_id = service.active_session.id
    assert service.update_session(session_id, subject.id, start, start + timedelta(hours=1)) is None
    assert database.get_session(session_id).end_time is None


def test_missing_heartbeat_recovery_keeps_zero_second_record(database, service, subject):
    start = datetime(2026, 6, 20, 9)
    service.start_subject(subject.id, start)
    session_id = service.active_session.id
    service.resolve_recovery(start)
    recorded = database.get_session(session_id)
    assert recorded is not None and recorded.end_time == start.isoformat()
    assert recorded.duration_seconds == 0
    assert service.active_session is None


def test_archived_switch_target_keeps_current_session(database, service, subject):
    other = service.add_subject("Archived", "#123456", "")
    service.archive_subject(other.id)
    service.start_subject(subject.id, datetime(2026, 6, 20, 9))
    current_id = service.active_session.id
    assert service.switch_subject(other.id, datetime(2026, 6, 20, 10)) is False
    assert database.get_session(current_id).end_time is None


def test_switch_uses_one_exact_moment(database, service, subject):
    other = service.add_subject("Other", "#123456", "")
    service.start_subject(subject.id, datetime(2026, 6, 20, 9))
    current_id = service.active_session.id
    moment = datetime(2026, 6, 20, 10, 15, 0, 123456)
    assert service.switch_subject(other.id, moment)
    assert database.get_session(current_id).end_time == moment.isoformat()
    assert service.active_session.start_time == moment.isoformat()


def test_switch_insert_failure_keeps_original_timer(database, service, subject, monkeypatch):
    other = service.add_subject("Other", "#123456", "")
    service.start_subject(subject.id, datetime(2026, 6, 20, 9))
    current_id = service.active_session.id

    def fail_insert(*args, **kwargs):
        raise RuntimeError("injected disk failure")

    monkeypatch.setattr(database, "start_session", fail_insert)
    with pytest.raises(RuntimeError, match="injected disk failure"):
        service.switch_subject(other.id, datetime(2026, 6, 20, 10, 15))
    assert database.get_session(current_id).end_time is None
    assert service.active_session.id == current_id


def test_offset_timestamps_are_rejected_by_local_time_model(service, subject):
    assert timeutils.parse_iso("2026-06-20T09:00:00+02:00") is None
    assert not service.start_subject(subject.id, datetime(2026, 6, 20, 9, tzinfo=timezone.utc))
    assert service.add_session(
        subject.id, datetime(2026, 6, 20, 9, tzinfo=timezone.utc), datetime(2026, 6, 20, 10)
    ) is None


@pytest.fixture
def timer_api(tmp_path, monkeypatch):
    client_module = pytest.importorskip("fastapi.testclient")
    from server import app as server_app, auth
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("phone")
    app_state = server_app.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(server_app._state, "db", app_state["db"])
    monkeypatch.setitem(server_app._state, "svc", app_state["svc"])
    client = client_module.TestClient(server_app.app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield client, app_state, server_app
    finally:
        server_app._state["db"].connection.close()


def _send(client, operation, params, op_id=None):
    return client.post("/ops", json={"ops": [{
        "op_id": op_id or str(uuid.uuid4()), "op": operation, "params": params,
    }]})


def _result(response):
    assert response.status_code == 200, response.text
    return response.json()["applied"][0]["result"]


def _subject(client, name="Subject"):
    return _result(_send(client, "add_subject", {"name": name}))["subject"]["uid"]


def _start(client, subject_uid, start, uid=None):
    return _result(_send(client, "start_subject", {
        "subject_uid": subject_uid, "start_time": start.isoformat(), "uid": uid or str(uuid.uuid4()),
    }))["session"]


def test_offline_start_stop_retains_actual_75_minutes(timer_api):
    client, _, _ = timer_api
    start = datetime(2026, 6, 20, 9)
    session = _start(client, _subject(client), start)
    stopped = _result(_send(client, "stop_active_subject", {
        "session_uid": session["uid"], "end_time": (start + timedelta(minutes=75)).isoformat(),
    }))
    assert stopped["session"]["duration_seconds"] == 4500
    assert stopped["session"]["start_time"] == start.isoformat()


def test_delayed_old_stop_does_not_stop_newer_timer(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    start = datetime(2026, 6, 20, 9)
    old = _start(client, subject_uid, start)
    newer = _start(client, subject_uid, start + timedelta(hours=2))
    stopped = _result(_send(client, "stop_active_subject", {
        "session_uid": old["uid"], "end_time": (start + timedelta(minutes=75)).isoformat(),
    }))
    assert stopped["session"]["duration_seconds"] == 4500
    assert client.get("/api/active").json()["active"]["uid"] == newer["uid"]
    _result(_send(client, "stop_active_subject", {
        "session_uid": old["uid"], "end_time": (start + timedelta(hours=5)).isoformat(),
    }))
    assert client.get("/api/active").json()["active"]["uid"] == newer["uid"]


def test_offline_start_while_newer_timer_runs_preserves_both(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    start = datetime(2026, 6, 20, 9)
    newer = _start(client, subject_uid, start + timedelta(hours=2))
    offline = _start(client, subject_uid, start)
    _result(_send(client, "stop_active_subject", {
        "session_uid": offline["uid"], "end_time": (start + timedelta(minutes=75)).isoformat(),
    }))
    assert client.get("/api/active").json()["active"]["uid"] == newer["uid"]
    sessions = client.get("/api/snapshot").json()["sessions"]
    assert len(sessions) == 2
    assert next(s for s in sessions if s["uid"] == offline["uid"])["duration_seconds"] == 4500


def test_retry_with_new_op_id_and_same_session_uid_creates_no_duplicate(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    uid = str(uuid.uuid4())
    first = _start(client, subject_uid, datetime(2026, 6, 20, 9), uid)
    second = _start(client, subject_uid, datetime(2026, 6, 20, 9), uid)
    assert first["uid"] == second["uid"]
    assert len(client.get("/api/snapshot").json()["sessions"]) == 1


def test_offline_timer_for_now_archived_subject_is_preserved(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    _result(_send(client, "archive_subject", {"subject_uid": subject_uid}))
    start = datetime(2026, 6, 20, 9)
    session = _start(client, subject_uid, start)
    stopped = _result(_send(client, "stop_active_subject", {
        "session_uid": session["uid"], "end_time": (start + timedelta(minutes=75)).isoformat(),
    }))
    assert stopped["session"]["duration_seconds"] == 4500


def test_deleted_parent_blocks_recorded_offline_start_instead_of_acknowledging_loss(timer_api):
    client, app_state, _ = timer_api
    subject_uid = _subject(client)
    _result(_send(client, "delete_subject", {"subject_uid": subject_uid}))
    op_id = str(uuid.uuid4())
    response = _send(client, "start_subject", {
        "subject_uid": subject_uid, "uid": str(uuid.uuid4()),
        "start_time": "2026-06-20T09:00:00",
    }, op_id)
    assert response.status_code == 404
    assert app_state["db"].connection.execute(
        "SELECT response FROM applied_ops WHERE op_id = ?", (op_id,)
    ).fetchone() is None


def test_targeted_heartbeat_cannot_touch_a_different_timer(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    start = datetime(2026, 6, 20, 9)
    old = _start(client, subject_uid, start)
    newer = _start(client, subject_uid, start + timedelta(hours=2))
    _result(_send(client, "heartbeat", {
        "session_uid": old["uid"], "moment": (start + timedelta(minutes=75)).isoformat(),
    }))
    rows = {s["uid"]: s for s in client.get("/api/snapshot").json()["sessions"]}
    assert rows[old["uid"]]["last_active_at"] == (start + timedelta(minutes=75)).isoformat()
    assert rows[newer["uid"]]["last_active_at"] == newer["start_time"]


def test_switch_targets_original_timer_and_adopts_new_uid(timer_api):
    client, _, _ = timer_api
    subject_uid = _subject(client)
    other_uid = _subject(client, "Other")
    start = datetime(2026, 6, 20, 9)
    old = _start(client, subject_uid, start)
    newer = _start(client, subject_uid, start + timedelta(hours=3))
    wanted = str(uuid.uuid4())
    moment = start + timedelta(minutes=75)
    switched = _result(_send(client, "switch_subject", {
        "subject_uid": other_uid, "source_session_uid": old["uid"], "uid": wanted,
        "moment": moment.isoformat(),
    }))
    assert switched["session"]["uid"] == wanted
    rows = {s["uid"]: s for s in client.get("/api/snapshot").json()["sessions"]}
    assert rows[old["uid"]]["duration_seconds"] == 4500
    assert rows[newer["uid"]]["end_time"] is None
    assert rows[wanted]["start_time"] == moment.isoformat()


def test_server_restart_preserves_all_open_timers(timer_api):
    client, app_state, server_app = timer_api
    subject_uid = _subject(client)
    first = _start(client, subject_uid, datetime(2026, 6, 20, 9))
    second = _start(client, subject_uid, datetime(2026, 6, 20, 10))
    path = app_state["db"].db_path
    app_state["db"].connection.execute("UPDATE sessions SET device_id = NULL")
    app_state["db"].connection.commit()
    app_state["db"].connection.close()
    restarted = server_app.build_state(path)
    server_app._state.update(restarted)
    rows = client.get("/api/snapshot").json()["sessions"]
    assert {s["uid"] for s in rows} == {first["uid"], second["uid"]}
    assert all(s["end_time"] is None for s in rows)


def test_recovery_operation_retains_short_sessions(timer_api):
    client, _, _ = timer_api
    start = datetime(2026, 6, 20, 9)
    session = _start(client, _subject(client), start)
    result = _result(_send(client, "resolve_recovery", {
        "session_uid": session["uid"], "end_time": start.isoformat(),
    }))
    assert result["resolved"]
    assert result["session"]["duration_seconds"] == 0
    assert len(client.get("/api/snapshot").json()["sessions"]) == 1


@pytest.mark.parametrize("bad_time", ["", None, "yesterday", "2026-06-20T10:00:00+02:00", 42])
def test_malformed_switch_timestamp_keeps_existing_timer(timer_api, bad_time):
    client, _, _ = timer_api
    old = _start(client, _subject(client), datetime(2026, 6, 20, 9))
    response = _send(client, "switch_subject", {
        "subject_uid": _subject(client, "Other"), "source_session_uid": old["uid"],
        "uid": str(uuid.uuid4()), "moment": bad_time,
    })
    assert response.status_code == 400
    assert client.get("/api/active").json()["active"]["uid"] == old["uid"]


@pytest.mark.parametrize("bad_time", ["", None, "2026-06-20T10:00:00+02:00", 42])
def test_malformed_stop_timestamp_keeps_existing_timer(timer_api, bad_time):
    client, _, _ = timer_api
    old = _start(client, _subject(client), datetime(2026, 6, 20, 9))
    response = _send(client, "stop_active_subject", {
        "session_uid": old["uid"], "end_time": bad_time,
    })
    assert response.status_code == 400
    assert client.get("/api/active").json()["active"]["uid"] == old["uid"]


def test_crash_before_retry_receipt_rolls_back_timer_write(timer_api, monkeypatch):
    from server import ops
    client, app_state, _ = timer_api
    subject_uid = _subject(client)
    original = ops._HANDLERS["start_subject"]

    def crash_after_start(service, params):
        original(service, params)
        raise RuntimeError("injected process interruption")

    monkeypatch.setitem(ops._HANDLERS, "start_subject", crash_after_start)
    with pytest.raises(RuntimeError, match="injected process interruption"):
        _send(client, "start_subject", {
            "subject_uid": subject_uid, "uid": str(uuid.uuid4()),
            "start_time": "2026-06-20T09:00:00",
        })
    assert app_state["db"].get_open_sessions() == []
    assert app_state["svc"].active_session is None

