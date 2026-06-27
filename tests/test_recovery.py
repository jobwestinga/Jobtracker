"""Active-session recovery: the service reconciles open sessions on startup.

Recovery is intentionally non-destructive for the primary session — an
unfinished session is resumed, never silently deleted.
"""

from datetime import datetime

from jobtracker.services.tracker_service import TrackerService


def test_single_open_session_is_resumed(database, subject):
    # Simulate a crash that left one session open.
    database.start_session(subject.id)

    recovered = TrackerService(database)
    assert recovered.active_session is not None
    assert recovered.active_subject is not None
    assert recovered.active_subject.id == subject.id
    # The unfinished session is still open, not deleted.
    assert len(database.get_open_sessions()) == 1


def test_multiple_open_sessions_collapse_to_one(database, subject):
    # Two parallel open sessions should never both survive.
    database.start_session(subject.id)
    database.start_session(subject.id)
    assert len(database.get_open_sessions()) == 2

    recovered = TrackerService(database)
    assert recovered.active_session is not None
    assert len(database.get_open_sessions()) == 1


def test_recovery_preserves_last_active_at(database, subject):
    sess = database.start_session(subject.id)
    heartbeat = datetime(2026, 6, 20, 12, 0, 0).isoformat()
    database.touch_active_session(sess.id, heartbeat)

    recovered = TrackerService(database)
    assert recovered.active_session is not None
    assert recovered.active_session.last_active_at == heartbeat


def test_no_open_sessions_means_idle(database, subject):
    recovered = TrackerService(database)
    assert recovered.active_session is None
    assert recovered.active_subject is None
