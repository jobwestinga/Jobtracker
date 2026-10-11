"""Compatibility contracts for the composed TrackerService facade."""

from datetime import date, datetime, timedelta
import inspect
import json

import pytest

from jobtracker.core.database import Database
from jobtracker.services.tracker_service import TrackerService
from jobtracker.sync.service import SyncedTrackerService
from jobtracker.sync import state


def test_components_share_one_timer_state_and_follow_replaced_storage(database, tmp_path):
    service = TrackerService(database, recover=False)
    for component in (
        service._subjects, service._goals, service._recurrence,
        service._sessions, service._analytics, service._timer,
    ):
        assert vars(component) == {"_tracker": service}

    replacement = Database(tmp_path / "replacement.db")
    try:
        service.db = replacement
        subject = service.add_subject("Replacement store", "#123456", "")
        start = datetime(2026, 10, 11, 9)
        assert service.start_subject(subject.id, start)
        service.heartbeat_active_session(start + timedelta(minutes=75))
        assert service.active_session.last_active_at == "2026-10-11T10:15:00"
        assert database.get_all_subjects() == []
        assert replacement.get_open_session().uid == service.active_session.uid
        service.stop_active_subject(start + timedelta(minutes=75))
        assert service.active_session is None
        assert service.active_subject is None
        assert replacement.get_sessions_for_subject(subject.id)[0].duration_seconds == 4500
    finally:
        replacement.connection.close()


def test_subject_archive_calls_facade_stop_override(database):
    calls = []

    class Adapter(TrackerService):
        def stop_active_subject(self, end_time=None):
            calls.append(self.active_session.uid)
            return super().stop_active_subject(end_time)

    service = Adapter(database, recover=False)
    subject = service.add_subject("Archive", "#123456", "")
    service.start_subject(subject.id, datetime.now() - timedelta(minutes=75))
    uid = service.active_session.uid
    service.archive_subject(subject.id)
    assert calls == [uid]
    assert database.get_subject(subject.id).is_archived
    assert database.get_open_sessions() == []


def test_goal_completion_honors_facade_gate_override(database):
    class Adapter(TrackerService):
        def can_complete_goal(self, goal_id):
            return False

    service = Adapter(database, recover=False)
    goal = service.add_todo_task("Externally gated", "", None)
    assert not service.complete_goal(goal.id)
    assert not service.get_goal(goal.id).is_completed


def test_template_creation_honors_facade_serialization_override(database):
    class Adapter(TrackerService):
        @staticmethod
        def build_milestones_json(titles):
            return json.dumps([{"title": "Adapter milestone", "note": "kept"}])

    service = Adapter(database, recover=False)
    template = service.add_goal_template("Daily {date}", "", "daily", ["Original"])
    generated = service.generate_due_goal_instances(datetime(2026, 10, 11, 2, 30))
    assert json.loads(template.milestones_json)[0]["title"] == "Adapter milestone"
    assert service.get_goal(generated[0]).name == "Daily 2026-10-10"
    assert service.get_goal_milestones(generated[0])[0].note == "kept"
    assert service.generate_due_goal_instances(datetime(2026, 10, 11, 2, 45)) == []


def test_duplicate_and_shift_dispatch_facade_mutations(database):
    calls = []

    class Adapter(TrackerService):
        def add_session(self, subject_id, start_time, end_time):
            calls.append(("add", start_time))
            return super().add_session(subject_id, start_time, end_time)

        def update_session(self, session_id, subject_id, start_time, end_time):
            calls.append(("update", start_time))
            return super().update_session(session_id, subject_id, start_time, end_time)

    service = Adapter(database, recover=False)
    subject = service.add_subject("Copies", "#123456", "")
    start = datetime(2026, 10, 10, 1, 30)
    original = service.add_session(subject.id, start, start + timedelta(minutes=75))
    calls.clear()
    duplicate = service.duplicate_session(original.id, "next_day")
    shifted = service.shift_session(original.id, 900)
    assert calls == [("add", start + timedelta(days=1)), ("update", start + timedelta(minutes=15))]
    assert duplicate.duration_seconds == shifted.duration_seconds == 4500


def test_synced_switch_queue_failure_restores_same_shared_timer(database, monkeypatch):
    service = SyncedTrackerService(database, device_id="test-device", recover=False)
    source = service.add_subject("Source", "#123456", "")
    target = service.add_subject("Target", "#654321", "")
    start = datetime(2026, 10, 11, 9)
    service.start_subject(source.id, start)
    uid = service.active_session.uid
    queued = state.pending_count(database.connection)

    def fail_enqueue(*args, **kwargs):
        raise OSError("Synthetic outbox failure")

    monkeypatch.setattr(state, "enqueue", fail_enqueue)
    with pytest.raises(OSError, match="outbox"):
        service.switch_subject(target.id, start + timedelta(minutes=75))
    assert service.active_session.uid == uid
    assert service.active_subject.id == source.id
    assert database.get_open_session().uid == uid
    assert len(database.get_open_sessions()) == 1
    assert state.pending_count(database.connection) == queued
    assert not database.connection.in_transaction


def test_synced_composed_actions_keep_exact_operation_sequence(database):
    service = SyncedTrackerService(database, recover=False)
    source = service.add_subject("Source", "#123456", "")
    target = service.add_subject("Target", "#654321", "")
    start = datetime(2026, 10, 11, 9)
    service.start_subject(source.id, start)
    old_uid = service.active_session.uid
    assert service.switch_subject(target.id, start + timedelta(minutes=75))
    new_uid = service.active_session.uid
    queued = state.pending(database.connection)
    assert [row["op"] for row in queued] == ["add_subject", "add_subject", "start_subject", "switch_subject"]
    assert queued[-1]["params"]["source_session_uid"] == old_uid
    assert queued[-1]["params"]["uid"] == new_uid
    assert queued[-1]["params"]["moment"] == "2026-10-11T10:15:00"


def test_analytics_keeps_facade_window_override(database):
    class Adapter(TrackerService):
        def graph_end_day(self, day_start=None):
            return date(2026, 10, 15)

    service = Adapter(database, recover=False)
    start, end = service._resolve_logical_window(service.get_day_start(), 7, None, None)
    assert end == date(2026, 10, 15)
    assert start <= end


@pytest.mark.parametrize("method, keyword", [
    ("start_subject", "allow_parallel"),
    ("resolve_recovery", "session_id"),
    ("upsert_weight", "weight_uid"),
    ("_adopt_open_session", "include_other_devices"),
])
def test_facade_targeting_options_stay_keyword_only(method, keyword):
    parameter = inspect.signature(getattr(TrackerService, method)).parameters[keyword]
    assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
