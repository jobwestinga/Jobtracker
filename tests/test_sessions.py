"""Session lifecycle: start/stop, 30s rule, manual CRUD, overlap, heartbeat."""

from datetime import datetime, timedelta


def _backdate_active_start(database, service, minutes: int) -> None:
    """Move the active session's start_time into the past so stop() yields a
    deterministic, well-over-30s duration without real waiting."""
    past = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    database.connection.execute(
        "UPDATE sessions SET start_time = ? WHERE id = ?",
        (past, service.active_session.id),
    )
    database.connection.commit()
    service.active_session.start_time = past


# ── start / stop ─────────────────────────────────────────────────────────────
def test_start_creates_open_session(service, subject):
    assert service.start_subject(subject.id) is True
    assert service.active_session is not None
    assert service.active_session.end_time is None
    assert service.active_subject.id == subject.id


def test_only_one_active_session_allowed(service, subject):
    assert service.start_subject(subject.id) is True
    # Second start while one is active must be refused.
    assert service.start_subject(subject.id) is False
    assert len(service.db.get_open_sessions()) == 1


def test_stop_records_duration_for_real_session(database, service, subject):
    service.start_subject(subject.id)
    _backdate_active_start(database, service, minutes=10)
    service.stop_active_subject()

    assert service.active_session is None
    sessions = service.get_sessions_for_subject(subject.id)
    assert len(sessions) == 1
    assert sessions[0].end_time is not None
    assert sessions[0].duration_seconds >= 590  # ~600s, allow tiny clock drift


def test_stop_under_30s_discards_session(service, subject):
    # Start and immediately stop -> duration < 30s -> intentionally dropped.
    service.start_subject(subject.id)
    service.stop_active_subject()
    assert service.active_session is None
    assert service.get_sessions_for_subject(subject.id) == []


# ── manual add / edit / delete ───────────────────────────────────────────────
def test_manual_add_session(service, subject):
    start = datetime(2026, 6, 20, 9, 0)
    end = datetime(2026, 6, 20, 10, 0)
    sess = service.add_session(subject.id, start, end, note="reading")
    assert sess is not None
    assert sess.duration_seconds == 3600
    assert sess.note == "reading"


def test_manual_add_under_30s_is_rejected(service, subject):
    start = datetime(2026, 6, 20, 9, 0, 0)
    end = datetime(2026, 6, 20, 9, 0, 20)  # 20s
    assert service.add_session(subject.id, start, end) is None
    assert service.get_sessions_for_subject(subject.id) == []


def test_manual_edit_session(service, subject):
    sess = service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )
    updated = service.update_session(
        sess.id, subject.id,
        datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 11, 0),
        note="extended",
    )
    assert updated.duration_seconds == 7200
    assert updated.note == "extended"


def test_delete_session(service, subject):
    sess = service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )
    service.delete_session(sess.id)
    assert service.get_sessions_for_subject(subject.id) == []


# ── overlap (intentionally allowed) ──────────────────────────────────────────
def test_overlapping_sessions_are_allowed(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 11, 0))
    service.add_session(subject.id, datetime(2026, 6, 20, 10, 0), datetime(2026, 6, 20, 12, 0))
    sessions = service.get_sessions_for_subject(subject.id)
    assert len(sessions) == 2  # both stored, no overlap rejection


# ── heartbeat / last-known-active ────────────────────────────────────────────
def test_start_seeds_last_active_at(service, subject):
    service.start_subject(subject.id)
    row = service.db.get_session(service.active_session.id)
    assert row.last_active_at is not None
    assert row.last_active_at == row.start_time


def test_heartbeat_updates_last_active_at(service, subject):
    service.start_subject(subject.id)
    moment = datetime(2026, 6, 20, 12, 0, 0)
    service.heartbeat_active_session(moment)

    row = service.db.get_session(service.active_session.id)
    assert row.last_active_at == moment.isoformat()


def test_heartbeat_noop_when_idle(service):
    # No active session -> must not raise.
    service.heartbeat_active_session()


def test_heartbeat_ignores_closed_session(service, subject):
    sess = service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )
    # Closed session should never be touched by the heartbeat write.
    service.db.touch_active_session(sess.id, datetime(2026, 6, 20, 12, 0).isoformat())
    assert service.db.get_session(sess.id).last_active_at is None
