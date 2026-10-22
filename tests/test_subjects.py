"""Subject creation, duplicate guard, archive vs delete, cascade."""

from datetime import datetime, timedelta

import pytest

from jobtracker.sync import state
from jobtracker.sync.service import SyncedTrackerService


def test_resume_subject_follows_real_timer_history_not_row_order_or_manual_future(service):
    first = service.add_subject("First in list", "#22C55E", "")
    recent = service.add_subject("Last tracked", "#38BDF8", "")
    assert service.get_last_tracked_subject() is None
    now = datetime.now()
    assert service.start_subject(recent.id, now - timedelta(minutes=20))
    service.stop_active_subject(now - timedelta(minutes=5))
    # A manual backfill or future slot does not represent a new timer action.
    service.add_session(first.id, now - timedelta(minutes=4), now - timedelta(minutes=1))
    service.add_session(first.id, now + timedelta(days=2), now + timedelta(days=2, hours=1))
    service.set_subject_order([first.id, recent.id])
    assert service.get_last_tracked_subject().uid == recent.uid
    from jobtracker.services.tracker_service import TrackerService
    reopened = TrackerService(service.db, recover=False)
    assert reopened.get_last_tracked_subject().uid == recent.uid


def test_resume_skips_archived_subject_and_keeps_legacy_history(service):
    older = service.add_subject("Earlier work", "#22C55E", "")
    latest = service.add_subject("Archived work", "#38BDF8", "")
    now = datetime.now()
    # Legacy imports do not have device owners, but still provide a fallback.
    service.add_session(older.id, now - timedelta(hours=3), now - timedelta(hours=2))
    service.add_session(latest.id, now - timedelta(hours=1), now - timedelta(minutes=10))
    assert service.get_last_tracked_subject().id == latest.id
    service.archive_subject(latest.id)
    assert service.get_last_tracked_subject().id == older.id
    service.archive_subject(older.id)
    assert service.get_last_tracked_subject() is None


def test_add_subject(service):
    s = service.add_subject("Maths", "#22C55E", "notes")
    assert s is not None
    assert s.id is not None
    assert s.name == "Maths"
    assert s.color == "#22C55E"


def test_duplicate_name_rejected(service):
    assert service.add_subject("Maths", "#22C55E", "") is not None
    # Case-insensitive duplicate must be refused.
    assert service.add_subject("maths", "#EF4444", "") is None


def test_empty_name_rejected(service):
    assert service.add_subject("   ", "#22C55E", "") is None


def test_edit_running_subject_updates_timer_label_without_changing_session(service, subject):
    start = datetime(2026, 10, 11, 9)
    service.start_subject(subject.id, start)
    timer = service.active_session
    before = dict(service.db.connection.execute("SELECT * FROM sessions WHERE id = ?", (timer.id,)).fetchone())

    updated = service.update_subject(subject.id, " Renamed ", "#123456", " fresh notes ")

    assert service.active_subject == updated
    assert service.active_subject.name == "Renamed"
    assert service.active_subject.color == "#123456"
    assert service.active_subject.notes == "fresh notes"
    assert service.active_session is timer
    assert dict(service.db.connection.execute("SELECT * FROM sessions WHERE id = ?", (timer.id,)).fetchone()) == before
    service.stop_active_subject(start + timedelta(minutes=75))
    assert service.db.get_session(timer.id).uid == timer.uid
    assert service.db.get_session(timer.id).duration_seconds == 4500


def test_rejected_subject_edit_keeps_running_label(service, subject):
    other = service.add_subject("Other", "#123456", "")
    service.start_subject(subject.id, datetime(2026, 10, 11, 9))
    before = service.active_subject
    assert service.update_subject(subject.id, other.name, "#654321", "") is None
    assert service.active_subject is before
    assert service.update_subject(other.id, "Another", "#654321", "") is not None
    assert service.active_subject is before


def test_failed_synced_subject_edit_restores_label_and_keeps_timer(database, monkeypatch):
    service = SyncedTrackerService(database, device_id="subject-edit-test", recover=False)
    subject = service.add_subject("Original", "#123456", "")
    service.start_subject(subject.id, datetime(2026, 10, 11, 9))
    timer_uid = service.active_session.uid
    queued = state.pending_count(database.connection)

    def fail(*args, **kwargs):
        raise OSError("Synthetic enqueue failure")

    monkeypatch.setattr(state, "enqueue", fail)
    with pytest.raises(OSError, match="enqueue failure"):
        service.update_subject(subject.id, "Renamed", "#654321", "new notes")

    assert service.active_subject.name == "Original"
    assert service.active_subject.color == "#123456"
    assert database.get_subject(subject.id).name == "Original"
    assert service.active_session.uid == timer_uid
    assert database.get_open_session().uid == timer_uid
    assert state.pending_count(database.connection) == queued


def test_archive_hides_from_active_list_but_keeps_data(service):
    s = service.add_subject("History", "#F59E0B", "")
    service.archive_subject(s.id)

    active_ids = [x.id for x in service.get_all_subjects(archived=False)]
    archived_ids = [x.id for x in service.get_all_subjects(archived=True)]
    all_ids = [x.id for x in service.get_all_subjects_including_archived()]

    assert s.id not in active_ids
    assert s.id in archived_ids
    assert s.id in all_ids  # still present, graphs can still see it


def test_unarchive_restores_to_active_list(service):
    s = service.add_subject("History", "#F59E0B", "")
    service.archive_subject(s.id)
    service.unarchive_subject(s.id)
    active_ids = [x.id for x in service.get_all_subjects(archived=False)]
    assert s.id in active_ids


def test_delete_subject_cascades_sessions(service):
    s = service.add_subject("Chem", "#8B5CF6", "")
    service.add_session(s.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0))
    assert len(service.get_sessions_for_subject(s.id)) == 1

    service.delete_subject(s.id)
    assert service.get_all_subjects_including_archived() == [] or \
        s.id not in [x.id for x in service.get_all_subjects_including_archived()]
    # Sessions are gone via ON DELETE CASCADE.
    assert service.get_sessions_for_subject(s.id) == []


def test_delete_active_subject_stops_timer_first(service):
    s = service.add_subject("Bio", "#06B6D4", "")
    service.start_subject(s.id)
    assert service.active_session is not None
    service.delete_subject(s.id)
    assert service.active_session is None
    assert service.db.get_open_sessions() == []


def test_active_and_archived_subject_orders_are_independent(service):
    first = service.add_subject("First", "#111111", "")
    second = service.add_subject("Second", "#222222", "")
    old_first = service.add_subject("Old first", "#333333", "")
    old_second = service.add_subject("Old second", "#444444", "")
    service.archive_subject(old_first.id)
    service.archive_subject(old_second.id)

    service.set_subject_order(
        [old_second.id, old_first.id], archived=True
    )
    assert [subject.id for subject in service.get_all_subjects(archived=True)] == [
        old_second.id,
        old_first.id,
    ]
    assert [subject.id for subject in service.get_all_subjects(archived=False)] == [
        first.id,
        second.id,
    ]

    service.set_subject_order([second.id, first.id], archived=False)
    assert [subject.id for subject in service.get_all_subjects(archived=False)] == [
        second.id,
        first.id,
    ]
    assert [subject.id for subject in service.get_all_subjects(archived=True)] == [
        old_second.id,
        old_first.id,
    ]
