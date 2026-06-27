"""Subject creation, duplicate guard, archive vs delete, cascade."""

from datetime import datetime


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
