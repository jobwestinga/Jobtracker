"""Session lifecycle: start/stop, 30s rule, manual CRUD, overlap, heartbeat."""

from datetime import datetime, time, timedelta

from jobtracker.core import timeutils


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
    sess = service.add_session(subject.id, start, end)
    assert sess is not None
    assert sess.duration_seconds == 3600


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
    )
    assert updated.duration_seconds == 7200


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


# ── moving a session to another subject ──────────────────────────────────────
def test_update_session_moves_between_subjects(service, subject):
    other = service.add_subject("Deep Work", "#22C55E", "")
    sess = service.add_session(
        subject.id,
        datetime(2026, 6, 20, 9, 0),
        datetime(2026, 6, 20, 10, 0),
    )

    moved = service.update_session(
        sess.id,
        other.id,
        datetime(2026, 6, 20, 9, 0),
        datetime(2026, 6, 20, 10, 0),
    )

    # Same session row, same times and duration — only the subject changed.
    assert moved.id == sess.id
    assert moved.subject_id == other.id
    assert moved.start_time == sess.start_time
    assert moved.end_time == sess.end_time
    assert moved.duration_seconds == 3600
    assert service.get_sessions_for_subject(subject.id) == []
    assert len(service.get_sessions_for_subject(other.id)) == 1


# ── duplicating a session ────────────────────────────────────────────────────
def _yesterday_at(hour: int) -> datetime:
    """A safely-in-the-past moment: yesterday at ``hour``."""
    return datetime.combine(
        (datetime.now() - timedelta(days=1)).date(), time(hour, 0)
    )


def test_duplicate_to_today_keeps_clock_time_and_duration(service, subject):
    start = _yesterday_at(9)
    original = service.add_session(
        subject.id, start, start + timedelta(hours=2)
    )

    copy = service.duplicate_session(original.id, to="today")

    assert copy is not None and copy.id != original.id
    copy_start = datetime.fromisoformat(copy.start_time)
    assert copy_start.time() == start.time()          # same clock time
    assert copy.duration_seconds == original.duration_seconds
    assert copy.subject_id == subject.id
    # Lands on the current logical day, and the original is untouched.
    assert timeutils.logical_day(copy_start) == timeutils.logical_day(datetime.now())
    assert service.db.get_session(original.id).start_time == original.start_time
    assert len(service.get_sessions_for_subject(subject.id)) == 2


def test_duplicate_next_day_moves_exactly_one_logical_day(service, subject):
    start = datetime.now() - timedelta(days=5)
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    original = service.add_session(subject.id, start, start + timedelta(hours=1))

    copy = service.duplicate_session(original.id, to="next_day")

    assert copy is not None
    copy_start = datetime.fromisoformat(copy.start_time)
    assert copy_start.time() == start.time()
    assert (
        timeutils.logical_day(copy_start)
        == timeutils.logical_day(start) + timedelta(days=1)
    )
    assert copy.duration_seconds == original.duration_seconds


def test_repeated_next_day_duplicates_walk_forward(service, subject):
    start = datetime.now() - timedelta(days=4)
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)
    current = service.add_session(subject.id, start, start + timedelta(hours=1))

    days = []
    for _ in range(3):
        current = service.duplicate_session(current.id, to="next_day")
        assert current is not None
        days.append(timeutils.logical_day(datetime.fromisoformat(current.start_time)))

    first_day = timeutils.logical_day(start)
    assert days == [first_day + timedelta(days=n) for n in (1, 2, 3)]
    assert len(service.get_sessions_for_subject(subject.id)) == 4


def test_duplicate_may_create_a_future_session(service, subject):
    # Planning ahead is allowed: "+1 day" on a session from today lands tomorrow.
    start = datetime.now() - timedelta(hours=2)
    original = service.add_session(subject.id, start, start + timedelta(hours=1))

    copy = service.duplicate_session(original.id, to="next_day")

    assert copy is not None
    copy_start = datetime.fromisoformat(copy.start_time)
    assert copy_start > datetime.now()
    assert copy.duration_seconds == original.duration_seconds
    assert len(service.get_sessions_for_subject(subject.id)) == 2


def test_duplicate_rejects_unknown_mode_and_missing_session(service, subject):
    start = _yesterday_at(9)
    original = service.add_session(subject.id, start, start + timedelta(hours=1))
    assert service.duplicate_session(original.id, to="whenever") is None
    assert service.duplicate_session(999_999, to="today") is None
    assert len(service.get_sessions_for_subject(subject.id)) == 1


def test_duplicate_ignores_the_open_session(service, subject):
    service.start_subject(subject.id)
    assert service.duplicate_session(service.active_session.id, to="today") is None


# ── nudging a session in time ────────────────────────────────────────────────
def test_shift_session_moves_both_ends_and_keeps_duration(service, subject):
    start = datetime(2026, 6, 20, 14, 0)
    original = service.add_session(subject.id, start, start + timedelta(hours=2))

    moved = service.shift_session(original.id, -3600)

    assert moved is not None and moved.id == original.id
    assert datetime.fromisoformat(moved.start_time) == start - timedelta(hours=1)
    assert datetime.fromisoformat(moved.end_time) == start + timedelta(hours=1)
    assert moved.duration_seconds == original.duration_seconds
    assert moved.subject_id == subject.id


def test_shift_session_accumulates_and_accepts_quarter_hours(service, subject):
    start = datetime(2026, 6, 20, 14, 0)
    original = service.add_session(subject.id, start, start + timedelta(hours=1))

    for _ in range(2):
        service.shift_session(original.id, -3600)
    for _ in range(2):
        service.shift_session(original.id, -900)

    moved = service.get_session(original.id)
    assert datetime.fromisoformat(moved.start_time) == datetime(2026, 6, 20, 11, 30)
    assert moved.duration_seconds == 3600


def test_shift_session_ignores_missing_and_open_sessions(service, subject):
    assert service.shift_session(999_999, 900) is None
    service.start_subject(subject.id)
    assert service.shift_session(service.active_session.id, 900) is None


# ── graph windows reach sessions scheduled ahead of today ────────────────────
def test_graph_end_day_is_today_without_future_sessions(service, subject):
    start = datetime.now() - timedelta(days=1)
    service.add_session(subject.id, start, start + timedelta(hours=1))
    assert service.graph_end_day() == timeutils.logical_day(datetime.now())


def test_graph_end_day_stretches_to_future_sessions(service, subject):
    future = datetime.now() + timedelta(days=3)
    service.add_session(subject.id, future, future + timedelta(hours=1))
    assert service.graph_end_day() == timeutils.logical_day(future)


def test_future_session_appears_in_breakdown_and_heatmap(service, subject):
    future = (datetime.now() + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    service.add_session(subject.id, future, future + timedelta(hours=1))

    future_day = timeutils.logical_day(future).isoformat()
    breakdown = service.get_subject_breakdown(grouping="daily", days=None)
    assert any(bucket["date"] == future_day for bucket in breakdown)
    assert any(row["date"] == future_day for row in service.get_heatmap_data(days=None))
