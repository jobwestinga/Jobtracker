"""Receipts, atomicity, outcomes and service dispatch through the engine host."""
from datetime import datetime, timedelta

import pytest

from jobtracker.core.database import Database
from jobtracker.desktop import commands, protocol, receipts
from jobtracker.sync import state as sync_state
from tests.support.desktop import InProcessEngine, request_id


@pytest.fixture
def engine(database, tmp_path):
    return InProcessEngine(database, tmp_path)


@pytest.fixture
def synced_engine(database, tmp_path):
    database.set_setting("sync_enabled", "1")
    return InProcessEngine(database, tmp_path)


def _outbox(engine):
    return [row["op"] for row in sync_state.pending(engine.service.db.connection)]


def _closed_session(engine, minutes=75, days_ago=2):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    start = (datetime.now() - timedelta(days=days_ago)).replace(hour=9, minute=0, second=0, microsecond=0)
    session = engine.ok("session.add", {
        "subject_uid": subject["uid"], "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=minutes)).isoformat(),
    })["session"]
    return subject, session


def test_retrying_a_committed_shift_replays_without_moving_again(synced_engine):
    _subject, session = _closed_session(synced_engine)
    before = len(_outbox(synced_engine))
    rid = request_id()
    first = synced_engine.call("session.shift", {"session_uid": session["uid"], "seconds": 900}, rid=rid)
    assert first["outcome"] == protocol.APPLIED
    moved = first["result"]["session"]
    # The reply is "lost"; the client retries the same request identity.
    second = synced_engine.call("session.shift", {"session_uid": session["uid"], "seconds": 900}, rid=rid)
    assert second["outcome"] == protocol.REPLAYED
    assert second["result"]["session"] == moved
    stored = synced_engine.service.db.get_session(synced_engine.service.db.id_for_uid("sessions", session["uid"]))
    assert stored.start_time == moved["start_time"]
    assert datetime.fromisoformat(moved["start_time"]) - datetime.fromisoformat(session["start_time"]) == timedelta(minutes=15)
    assert _outbox(synced_engine)[before:] == ["update_session"]
    count = synced_engine.service.db.connection.execute(
        f"SELECT COUNT(*) FROM {receipts.TABLE} WHERE request_id = ?", (rid,)).fetchone()[0]
    assert count == 1


def test_reusing_a_request_id_for_other_parameters_is_a_conflict(engine):
    _subject, session = _closed_session(engine)
    rid = request_id()
    engine.ok("session.shift", {"session_uid": session["uid"], "seconds": 900}, rid=rid)
    response = engine.call("session.shift", {"session_uid": session["uid"], "seconds": 3600}, rid=rid)
    assert response["outcome"] == protocol.CONFLICT
    stored = engine.service.get_session(engine.service.db.id_for_uid("sessions", session["uid"]))
    assert datetime.fromisoformat(stored.start_time) - datetime.fromisoformat(session["start_time"]) == timedelta(minutes=15)


def test_receipt_failure_rolls_back_the_mutation_and_its_outbox_entry(synced_engine, monkeypatch):
    before = _outbox(synced_engine)

    def fail(*_args, **_kwargs):
        raise receipts.sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(receipts, "store", fail)
    response = synced_engine.call("subject.add", {"name": "Maths", "color": "#22C55E", "notes": ""})
    assert response["outcome"] == protocol.STORAGE_ERROR
    assert response["topics"] == []
    assert synced_engine.service.db.get_subject_by_name("Maths") is None
    assert _outbox(synced_engine) == before


def test_receipt_failure_after_timer_start_readopts_surviving_state(synced_engine, monkeypatch):
    subject = synced_engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    monkeypatch.setattr(receipts, "store", lambda *a, **k: (_ for _ in ()).throw(receipts.sqlite3.OperationalError("full")))
    response = synced_engine.call("timer.start", {"subject_uid": subject["uid"], "at": datetime.now().isoformat()})
    assert response["outcome"] == protocol.STORAGE_ERROR
    assert synced_engine.service.active_session is None
    assert synced_engine.service.db.get_open_sessions() == []
    assert "start_subject" not in _outbox(synced_engine)


def test_invalid_short_edit_keeps_the_original_row(engine):
    _subject, session = _closed_session(engine)
    start = datetime.fromisoformat(session["start_time"])
    response = engine.call("session.update", {
        "session_uid": session["uid"], "subject_uid": session["subject_uid"],
        "start_time": session["start_time"], "end_time": (start + timedelta(seconds=20)).isoformat(),
    })
    assert response["outcome"] == protocol.REFUSED
    stored = engine.ok("session.get", {"session_uid": session["uid"]})["session"]
    assert stored["end_time"] == session["end_time"]
    assert stored["duration_seconds"] == 75 * 60


def test_unchanged_fractional_endpoint_keeps_its_exact_string(engine, database):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    start = datetime(2026, 3, 1, 9, 0, 0, 123456)
    created = engine.ok("session.add", {
        "subject_uid": subject["uid"], "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=1, microseconds=654321)).isoformat(),
    })["session"]
    edited = engine.ok("session.update", {
        "session_uid": created["uid"], "subject_uid": subject["uid"],
        "start_time": created["start_time"], "end_time": "2026-03-01T11:00:00",
    })["session"]
    assert edited["start_time"] == "2026-03-01T09:00:00.123456"
    assert edited["end_time"] == "2026-03-01T11:00:00"


def test_missing_targets_and_domain_refusals_keep_their_meaning(engine):
    response = engine.call("session.shift", {"session_uid": "00000000-0000-4000-8000-000000000000", "seconds": 900})
    assert response["outcome"] == protocol.MISSING
    goal = engine.ok("goal.add", {"name": "Thesis", "notes": "", "deadline": None})["goal"]
    engine.ok("milestone.add", {"goal_uid": goal["uid"], "title": "Draft", "note": ""})
    rid = request_id()
    refused = engine.call("goal.complete", {"goal_uid": goal["uid"]}, rid=rid)
    assert refused["outcome"] == protocol.REFUSED and "milestone" in refused["error"]
    # A refusal is a committed outcome: retrying the same request replays it.
    assert engine.call("goal.complete", {"goal_uid": goal["uid"]}, rid=rid)["outcome"] == protocol.REPLAYED
    assert engine.call("subject.add", {"name": 5, "color": "#fff"})["outcome"] == protocol.INVALID


def test_unknown_or_malformed_frames_are_answered_without_side_effects(engine):
    engine.frames.clear()
    engine.host.handle_frame(b'{"protocol_version":1,"kind":"command","request_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","command":"db.execute","params":{}}')
    assert engine.frames[-1]["outcome"] == protocol.INVALID
    engine.host.handle_frame(b'{"protocol_version":9,"kind":"command"}')
    assert engine.frames[-1]["outcome"] == protocol.INVALID


def test_committed_changes_publish_topics_and_revision_after_the_response(engine):
    engine.frames.clear()
    engine.host.execute(request_id(), commands.REGISTRY["subject.add"], {"name": "Physics", "color": "#3B82F6", "notes": ""})
    kinds = [(frame["kind"], frame.get("event")) for frame in engine.frames]
    assert kinds[0] == ("response", None)
    assert ("event", "changed") in kinds
    changed = next(frame for frame in engine.frames if frame.get("event") == "changed")
    assert changed["topics"] == ["subjects"] and changed["revision"] == engine.host.revision == 1
    engine.frames.clear()
    engine.host.execute(request_id(), commands.REGISTRY["subjects.list"], {})
    assert all(frame.get("event") != "changed" for frame in engine.frames)


def test_timer_commands_use_captured_times_and_target_the_exact_session(engine):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    started_at = (datetime.now() - timedelta(minutes=50)).replace(microsecond=0)
    timer = engine.ok("timer.start", {"subject_uid": subject["uid"], "at": started_at.isoformat()})
    assert timer["active"]["start_time"] == started_at.isoformat()
    assert timer["subject"]["uid"] == subject["uid"]
    wrong = engine.call("timer.stop", {"session_uid": "00000000-0000-4000-8000-000000000000", "at": datetime.now().isoformat()})
    assert wrong["outcome"] == protocol.REFUSED
    stop_at = started_at + timedelta(minutes=45)
    stopped = engine.ok("timer.stop", {"session_uid": timer["active"]["uid"], "at": stop_at.isoformat()})
    assert stopped["kept"] is True and stopped["timer"]["active"] is None
    row = engine.ok("session.get", {"session_uid": timer["active"]["uid"]})["session"]
    assert row["end_time"] == stop_at.isoformat() and row["duration_seconds"] == 45 * 60


def test_short_stop_follows_the_existing_discard_rule(engine):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    now = datetime.now().replace(microsecond=0)
    timer = engine.ok("timer.start", {"subject_uid": subject["uid"], "at": (now - timedelta(seconds=10)).isoformat()})
    stopped = engine.ok("timer.stop", {"session_uid": timer["active"]["uid"], "at": now.isoformat()})
    assert stopped["kept"] is False


def test_switch_requires_the_expected_source_and_keeps_one_timer(engine):
    a = engine.ok("subject.add", {"name": "A", "color": "#3B82F6", "notes": ""})["subject"]
    b = engine.ok("subject.add", {"name": "B", "color": "#22C55E", "notes": ""})["subject"]
    start = datetime.now().replace(microsecond=0) - timedelta(hours=1)
    timer = engine.ok("timer.start", {"subject_uid": a["uid"], "at": start.isoformat()})
    stale = engine.call("timer.switch", {"subject_uid": b["uid"], "source_session_uid": None, "at": datetime.now().isoformat()})
    assert stale["outcome"] == protocol.REFUSED
    switched = engine.ok("timer.switch", {
        "subject_uid": b["uid"], "source_session_uid": timer["active"]["uid"],
        "at": (start + timedelta(minutes=30)).isoformat(),
    })
    assert switched["subject"]["uid"] == b["uid"]
    assert len(engine.service.db.get_open_sessions()) == 1


def test_resume_uses_timer_history_not_card_order(engine):
    a = engine.ok("subject.add", {"name": "A", "color": "#3B82F6", "notes": ""})["subject"]
    b = engine.ok("subject.add", {"name": "B", "color": "#22C55E", "notes": ""})["subject"]
    start = datetime.now() - timedelta(hours=3)
    engine.ok("session.add", {"subject_uid": b["uid"], "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()})
    engine.ok("subject.reorder", {"subject_uids": [b["uid"], a["uid"]]})
    listing = engine.ok("subjects.list", {})
    assert listing["resume_subject_uid"] == b["uid"]
    assert [s["uid"] for s in listing["subjects"]] == [b["uid"], a["uid"]]


def test_settings_writes_are_allowlisted_and_never_expose_secrets(engine):
    result = engine.ok("settings.set", {"values": {"theme_fx": "Space", "theme_palette": "Violet"}})
    assert result["values"]["theme_palette"] == "Violet"
    assert engine.call("settings.set", {"values": {"device_id": "x"}})["outcome"] == protocol.INVALID
    names = [theme["name"] for theme in result["themes"]]
    assert names == ["OLED Midnight", "Carbon Dark", "Ocean", "Slate", "Ocean Aura", "Violet Space"]
    assert "device_id" not in result["values"]


def test_sync_configuration_writes_token_file_without_echoing_it(engine, tmp_path, monkeypatch):
    token_file = tmp_path / "sync_token"
    monkeypatch.setenv("JOBTRACKER_SYNC_TOKEN_PATH", str(token_file))
    response = engine.call("sync.configure", {"enabled": True, "server_url": "https://example.invalid/", "token": "secret-token"})
    assert response["outcome"] == protocol.OK
    assert "secret-token" not in str(engine.frames)
    assert token_file.read_text() == "secret-token"
    assert response["result"]["has_token"] is True and response["result"]["server_url"] == "https://example.invalid"
    # An empty token keeps the saved credential.
    engine.ok("sync.configure", {"enabled": True, "server_url": "https://example.invalid", "token": ""})
    assert token_file.read_text() == "secret-token"
    count = engine.service.db.connection.execute(f"SELECT COUNT(*) FROM {receipts.TABLE}").fetchone()[0]
    assert count == 0


def test_receipts_are_local_bookkeeping_outside_backups_and_mirrors(engine, tmp_path):
    engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})
    export = engine.service.export_data()
    assert receipts.TABLE not in export
    assert receipts.TABLE not in str(export.keys())
    from jobtracker.core import sync_policy
    assert receipts.TABLE not in sync_policy.SYNCED_TABLES


def test_sessions_for_subject_are_paginated(engine):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    base = datetime(2026, 1, 1, 9)
    for index in range(5):
        start = base + timedelta(days=index)
        engine.ok("session.add", {"subject_uid": subject["uid"], "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()})
    page = engine.ok("sessions.for_subject", {"subject_uid": subject["uid"], "limit": 2})
    assert page["total"] == 5 and len(page["sessions"]) == 2 and page["next_offset"] == 2
    last = engine.ok("sessions.for_subject", {"subject_uid": subject["uid"], "offset": 4, "limit": 2})
    assert len(last["sessions"]) == 1 and last["next_offset"] is None


def test_quit_is_refused_while_genuinely_tracking_and_backs_up_otherwise(engine, tmp_path):
    subject = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["subject"]
    timer = engine.ok("timer.start", {"subject_uid": subject["uid"], "at": datetime.now().isoformat()})
    refused = engine.ok("app.quit")
    assert refused["quit"] is False and refused["reason"] == "active_session"
    engine.ok("timer.stop", {"session_uid": timer["active"]["uid"], "at": (datetime.now() + timedelta(minutes=1)).isoformat()})
    done = engine.ok("app.quit")
    assert done["quit"] is True and done["backup_error"] is None
    assert list((tmp_path / "backups").glob("autobackup_*.json"))
    after = engine.call("subjects.list")
    assert after["outcome"] == protocol.OK  # direct execute still answers reads
    engine.frames.clear()
    engine.host.handle_frame(b'{"protocol_version":1,"kind":"command","request_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","command":"subjects.list","params":{}}')
    assert engine.frames[-1]["outcome"] == protocol.UNAVAILABLE


def test_recovery_prompt_suppresses_heartbeat_until_settled(database, tmp_path):
    service_db = database
    from jobtracker.services.tracker_service import TrackerService

    seed = TrackerService(service_db)
    subject = seed.add_subject("Physics", "#3B82F6", "")
    start = datetime.now() - timedelta(hours=3)
    seed.start_subject(subject.id, start_time=start)
    seed.heartbeat_active_session(start + timedelta(minutes=30))
    engine = InProcessEngine(service_db, tmp_path)
    snapshot = engine.ok("timer.get")
    assert snapshot["recovery"] is not None and snapshot["recovery"]["gap_seconds"] > 3600
    before = engine.service.db.get_open_session().last_active_at
    engine.host._fire("heartbeat")
    assert engine.service.db.get_open_session().last_active_at == before
    resolved = engine.ok("recovery.resolve", {"session_uid": snapshot["active"]["uid"], "end_time": snapshot["recovery"]["last_active"]})
    assert resolved["active"] is None and resolved["recovery"] is None
    stored = engine.service.get_session(engine.service.db.id_for_uid("sessions", snapshot["active"]["uid"]))
    assert stored.duration_seconds == 30 * 60


def test_foreign_device_timer_is_reported_and_never_recovered(database, tmp_path):
    from jobtracker.services.tracker_service import TrackerService

    phone = TrackerService(database, device_id="phone")
    subject = phone.add_subject("Physics", "#3B82F6", "")
    phone.start_subject(subject.id, start_time=datetime.now() - timedelta(hours=5))
    engine = InProcessEngine(database, tmp_path)
    snapshot = engine.ok("timer.get")
    assert snapshot["active"] is None
    assert len(snapshot["foreign_open"]) == 1 and snapshot["foreign_open"][0]["owned"] is False
    assert database.get_open_sessions()[0].end_time is None


def test_mirror_replacement_keeps_receipts_queryable(database, tmp_path):
    engine = InProcessEngine(database, tmp_path)
    rid = request_id()
    created = engine.ok("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""}, rid=rid)["subject"]
    from jobtracker.core import sync_policy

    with database.atomic():
        for table in reversed(sync_policy.SYNCED_TABLES):
            database.connection.execute(f"DELETE FROM {table}")
    replay = engine.call("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""}, rid=rid)
    assert replay["outcome"] == protocol.REPLAYED and replay["result"]["subject"]["uid"] == created["uid"]
    assert database.get_subject_by_name("Physics") is None


def test_receipt_install_backs_up_an_existing_store_once(tmp_path):
    store = Database(tmp_path / "existing.db")
    try:
        store.add_subject("Physics", "#3B82F6", "")
        backup = receipts.install(store.connection, backup_dir=tmp_path / "migrations")
        assert backup is not None and backup.exists()
        copy = Database(backup)
        try:
            assert copy.get_subject_by_name("Physics") is not None
            assert not receipts.table_exists(copy.connection)
        finally:
            copy.connection.close()
        assert receipts.install(store.connection, backup_dir=tmp_path / "migrations") is None
    finally:
        store.connection.close()
