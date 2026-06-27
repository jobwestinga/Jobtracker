"""Safer subject deletion: summary facts + archive vs delete behaviour."""

from datetime import datetime


def test_summary_for_subject_without_sessions(service):
    s = service.add_subject("Empty", "#22C55E", "")
    summary = service.get_subject_deletion_summary(s.id)
    assert summary["session_count"] == 0
    assert summary["total_seconds"] == 0
    assert summary["earliest"] is None
    assert summary["latest"] is None


def test_summary_for_subject_with_sessions(service):
    s = service.add_subject("Busy", "#3B82F6", "")
    service.add_session(s.id, datetime(2026, 6, 10, 9, 0), datetime(2026, 6, 10, 10, 0))
    service.add_session(s.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 11, 0))

    summary = service.get_subject_deletion_summary(s.id)
    assert summary["session_count"] == 2
    assert summary["total_seconds"] == 3 * 3600
    assert summary["earliest"].startswith("2026-06-10")
    assert summary["latest"].startswith("2026-06-20")
    assert len(summary["sessions"]) == 2


def test_archive_preserves_history(service):
    s = service.add_subject("Keep", "#F59E0B", "")
    service.add_session(s.id, datetime(2026, 6, 10, 9, 0), datetime(2026, 6, 10, 10, 0))
    service.archive_subject(s.id)
    # Sessions survive archiving.
    assert len(service.get_sessions_for_subject(s.id)) == 1
    summary = service.get_subject_deletion_summary(s.id)
    assert summary["session_count"] == 1


def test_delete_cascades_sessions(service):
    s = service.add_subject("Gone", "#EF4444", "")
    service.add_session(s.id, datetime(2026, 6, 10, 9, 0), datetime(2026, 6, 10, 10, 0))
    service.delete_subject(s.id)
    assert service.get_sessions_for_subject(s.id) == []
    assert s.id not in [x.id for x in service.get_all_subjects_including_archived()]
