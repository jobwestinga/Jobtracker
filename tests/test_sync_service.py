"""Every write the app makes must become an operation.

A mutation that reaches SQLite without a matching queued operation puts the
mirror out of step with the server, and the integrity check "repairs" that by
re-downloading — silently discarding the user's change. So the important test
here is not any single method, it is the end-to-end one: do a large mixed batch
of edits offline, sync, and assert the server ends up byte-identical to the
mirror.
"""

import inspect
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from jobtracker.core import sync_policy  # noqa: E402
from jobtracker.core.database import Database  # noqa: E402
from jobtracker.services.tracker_service import TrackerService  # noqa: E402
from jobtracker.sync import state  # noqa: E402
from jobtracker.sync.engine import SyncEngine  # noqa: E402
from jobtracker.sync.service import SyncedTrackerService  # noqa: E402
from server import app as app_module  # noqa: E402
from server import auth, ops  # noqa: E402
from tests.test_sync_engine import InProcessClient  # noqa: E402

TestClient = fastapi_testclient.TestClient

# Everything on TrackerService that changes a synced table. If you add one,
# route it in SyncedTrackerService and add it here.
MUTATING_METHODS = [
    "add_subject", "update_subject", "archive_subject", "unarchive_subject",
    "delete_subject", "set_subject_order",
    "start_subject", "stop_active_subject", "switch_subject",
    "add_session", "update_session", "delete_session", "duplicate_session",
    "shift_session",
    "add_todo_task", "update_todo_task", "delete_todo_task", "complete_goal",
    "uncomplete_goal", "toggle_goal_focused", "set_todo_task_order",
    "add_milestone", "update_milestone", "set_milestone_done", "delete_milestone",
    "set_milestone_order",
    "add_goal_template", "update_goal_template", "set_goal_template_active",
    "delete_goal_template", "generate_due_goal_instances",
    "set_setting", "set_day_start",
]


@pytest.fixture
def synced(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("macbook")

    server_state = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server_state["db"])
    monkeypatch.setitem(app_module._state, "svc", server_state["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})

    mirror = Database(tmp_path / "mirror.db")
    svc = SyncedTrackerService(mirror, device_id="macbook")
    client = InProcessClient(http, token)
    engine = SyncEngine(mirror, client, device_id="macbook")

    class World:
        pass

    w = World()
    w.svc = svc
    w.mirror = mirror
    w.engine = engine
    w.client = client
    w.server_db = server_state["db"]
    w.http = http
    try:
        yield w
    finally:
        mirror.connection.close()
        server_state["db"].connection.close()


def queued_ops(w):
    return [e["op"] for e in state.pending(w.mirror.connection, limit=500)]


def table_counts(db):
    cur = db.connection.cursor()
    return {
        t: int(cur.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
        for t in sync_policy.SYNCED_TABLES
    }


def uid_fingerprint(db):
    cur = db.connection.cursor()
    return {
        t: sorted(r["uid"] for r in cur.execute(f"SELECT uid FROM {t}"))
        for t in sync_policy.SYNCED_TABLES
    }


# ── coverage of the write surface ───────────────────────────────────────


def test_every_mutating_method_is_routed(synced):
    missing = [
        name
        for name in MUTATING_METHODS
        if name not in vars(SyncedTrackerService)
    ]
    assert not missing, f"these writes would bypass the outbox: {missing}"


def test_overrides_keep_the_base_signature(synced):
    """A changed signature would break the UI at runtime, not at import."""
    for name in MUTATING_METHODS:
        base = inspect.signature(getattr(TrackerService, name))
        mine = inspect.signature(getattr(SyncedTrackerService, name))
        assert [
            (p.name, p.default) for p in base.parameters.values()
        ] == [(p.name, p.default) for p in mine.parameters.values()], name


def test_every_op_the_client_emits_exists_on_the_server(synced):
    """Catches a typo'd op name, which would otherwise only fail at sync time."""
    source = Path(inspect.getfile(SyncedTrackerService)).read_text()
    emitted = set(re.findall(r'_queue\(\s*"([a-z_]+)"', source))
    unknown = emitted - set(ops.known_ops())
    assert not unknown, f"client emits operations the server does not handle: {unknown}"
    assert len(emitted) > 20, "expected the client to emit most of the op surface"


# ── individual routing behaviour ────────────────────────────────────────


def test_creating_a_subject_queues_it_with_its_uid(synced):
    subject = synced.svc.add_subject("Physics", "#3B82F6", "")
    entry = state.pending(synced.mirror.connection)[0]

    assert entry["op"] == "add_subject"
    assert entry["params"]["uid"] == synced.mirror.uid_for_id("tasks", subject.id)


def test_delete_captures_the_uid_before_the_row_goes(synced):
    subject = synced.svc.add_subject("Physics", "#3B82F6", "")
    uid = synced.mirror.uid_for_id("tasks", subject.id)
    synced.svc.delete_subject(subject.id)

    delete_entry = [
        e for e in state.pending(synced.mirror.connection) if e["op"] == "delete_subject"
    ][0]
    assert delete_entry["params"]["subject_uid"] == uid


def test_heartbeats_are_never_queued(synced):
    subject = synced.svc.add_subject("Physics", "#3B82F6", "")
    synced.svc.start_subject(subject.id)
    before = len(queued_ops(synced))
    for _ in range(50):
        synced.svc.heartbeat_active_session()
    assert len(queued_ops(synced)) == before


def test_a_refused_completion_is_not_queued(synced):
    """The server would refuse it too, so queueing it would just block the queue."""
    goal = synced.svc.add_todo_task("Pass the exam", "", None)
    synced.svc.add_milestone(goal.id, "Chapter 1")
    assert synced.svc.complete_goal(goal.id) is False
    assert "complete_goal" not in queued_ops(synced)


def test_device_local_settings_are_not_queued(synced):
    synced.svc.set_setting("theme_fx", "Nebula")
    synced.svc.set_setting("graph_range", "months")
    assert queued_ops(synced) == []

    synced.svc.set_day_start("05:00")
    assert "set_setting" in queued_ops(synced)


def test_goal_generation_is_delegated_and_deduped(synced):
    """Both machines generating would each create a goal for the same period."""
    created = synced.svc.generate_due_goal_instances()
    assert created == []
    for _ in range(10):
        synced.svc.generate_due_goal_instances()
    assert queued_ops(synced).count("generate_due_goals") == 1


def test_stop_sends_the_end_time_it_actually_used(synced):
    """The op may sit in the outbox for hours; 'now' there is not 'now' here."""
    subject = synced.svc.add_subject("Physics", "#3B82F6", "")
    synced.svc.start_subject(subject.id)
    session_id = synced.svc.active_session.id
    end = datetime.now().replace(microsecond=0) + timedelta(minutes=45)
    synced.svc.stop_active_subject(end)

    stop = [
        e for e in state.pending(synced.mirror.connection)
        if e["op"] == "stop_active_subject"
    ][0]
    assert stop["params"]["end_time"] == synced.mirror.get_session(session_id).end_time


def test_a_sub_30_second_session_is_dropped_on_both_sides(synced):
    """The 30s rule deletes the session locally. The server must reach the same
    conclusion, which it only can if it is told the end time we used rather than
    falling back to its own clock."""
    subject = synced.svc.add_subject("Physics", "#3B82F6", "")
    synced.svc.start_subject(subject.id)
    session_id = synced.svc.active_session.id
    synced.svc.stop_active_subject()

    assert synced.mirror.get_session(session_id) is None, "30s rule should have dropped it"
    stop = [
        e for e in state.pending(synced.mirror.connection)
        if e["op"] == "stop_active_subject"
    ][0]
    assert stop["params"]["end_time"] is not None, "server would use its own clock"

    result = synced.engine.sync()
    assert result.ok
    assert table_counts(synced.server_db)["sessions"] == 0
    assert table_counts(synced.mirror)["sessions"] == 0


# ── the real proof: no drift after a mixed batch ────────────────────────


def test_a_full_days_worth_of_edits_converges_exactly(synced):
    svc = synced.svc
    now = datetime.now().replace(microsecond=0)

    physics = svc.add_subject("Physics", "#3B82F6", "notes")
    maths = svc.add_subject("Maths", "#EF4444", "")
    chem = svc.add_subject("Chemistry", "#10B981", "")

    svc.update_subject(maths.id, "Mathematics", "#F59E0B", "renamed")
    svc.archive_subject(chem.id)
    svc.set_subject_order([maths.id, physics.id], archived=False)

    first = svc.add_session(physics.id, now - timedelta(hours=3), now - timedelta(hours=2))
    second = svc.add_session(maths.id, now - timedelta(hours=6), now - timedelta(hours=5))
    svc.update_session(first.id, physics.id, now - timedelta(hours=3), now - timedelta(minutes=90))
    svc.shift_session(second.id, 900)
    duplicated = svc.duplicate_session(first.id, to="today")
    svc.delete_session(duplicated.id)

    svc.start_subject(physics.id)
    svc.stop_active_subject()

    goal = svc.add_todo_task("Pass the exam", "the big one", None)
    other = svc.add_todo_task("Read a book", "", None)
    svc.update_todo_task(other.id, "Read two books", "updated", None)
    svc.toggle_goal_focused(goal.id)
    m1 = svc.add_milestone(goal.id, "Chapter 1")
    m2 = svc.add_milestone(goal.id, "Chapter 2")
    svc.update_milestone(m2.id, "Chapter 2 (revised)", "note")
    svc.set_milestone_order(goal.id, [m2.id, m1.id])
    svc.set_milestone_done(m1.id, True)
    svc.set_milestone_done(m2.id, True)
    assert svc.complete_goal(goal.id) is True
    svc.uncomplete_goal(goal.id)
    svc.set_todo_task_order([other.id, goal.id], completed=False)

    template = svc.add_goal_template("Daily review", "", "daily", ["Inbox zero"])
    svc.update_goal_template(template.id, "Daily review v2", "", "weekly", ["Inbox zero"], 3)
    svc.set_goal_template_active(template.id, False)

    svc.set_day_start("04:00")

    # Nothing has reached the server yet.
    assert table_counts(synced.server_db) == {t: 0 for t in sync_policy.SYNCED_TABLES}
    assert state.pending_count(synced.mirror.connection) > 25

    result = synced.engine.sync()

    assert result.ok, result.error
    assert result.blocked_reason is None
    assert state.pending_count(synced.mirror.connection) == 0
    assert result.repaired is False, "a repair means something drifted"

    # The strong assertion: identical rows, identified identically, on both sides.
    assert table_counts(synced.mirror) == table_counts(synced.server_db)
    assert uid_fingerprint(synced.mirror) == uid_fingerprint(synced.server_db)
    assert synced.engine.verify(result) is True
    assert synced.server_db.get_setting("day_start_time") == "04:00"


def test_edits_made_offline_survive_and_converge(synced):
    svc = synced.svc
    synced.client.offline = True

    subject = svc.add_subject("Offline subject", "#3B82F6", "")
    now = datetime.now().replace(microsecond=0)
    svc.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))
    goal = svc.add_todo_task("Offline goal", "", None)
    svc.add_milestone(goal.id, "Step one")

    offline = synced.engine.sync()
    assert offline.offline is True
    assert table_counts(synced.server_db)["tasks"] == 0

    synced.client.offline = False
    online = synced.engine.sync()

    assert online.ok
    assert table_counts(synced.mirror) == table_counts(synced.server_db)
    assert uid_fingerprint(synced.mirror) == uid_fingerprint(synced.server_db)


def test_a_second_sync_after_convergence_changes_nothing(synced):
    svc = synced.svc
    subject = svc.add_subject("Physics", "#3B82F6", "")
    svc.add_todo_task("A goal", "", None)
    synced.engine.sync()

    before = uid_fingerprint(synced.mirror)
    second = synced.engine.sync()

    assert second.applied == 0
    assert second.deleted == 0
    assert second.repaired is False
    assert uid_fingerprint(synced.mirror) == before


# ── the outbox must never deadlock ──────────────────────────────────────


def test_deleting_something_the_other_device_already_deleted(synced):
    """The scenario that used to jam the queue forever.

    Delete a session on the phone while offline; delete the same one on the Mac
    and let that reach the server; reconnect. The queued delete can never
    succeed — and the outbox stops at the first refusal, so everything queued
    behind it would have been stuck for good.
    """
    svc = synced.svc
    subject = svc.add_subject("Physics", "#3B82F6", "")
    now = datetime.now().replace(microsecond=0)
    session = svc.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))
    synced.engine.sync()
    session_uid = synced.mirror.uid_for_id("sessions", session.id)

    # Someone else removes it.
    synced.http.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "delete_session",
        "params": {"session_uid": session_uid}}]})

    # Meanwhile this device deletes it too, offline, and does more work after.
    synced.client.offline = True
    svc.delete_session(session.id)
    svc.add_todo_task("Written after the delete", "", None)
    synced.engine.sync()
    assert state.pending_count(synced.mirror.connection) == 2

    synced.client.offline = False
    result = synced.engine.sync()

    assert result.ok, result.error
    assert result.blocked_reason is None
    assert state.pending_count(synced.mirror.connection) == 0
    # The work queued *behind* the impossible delete still arrived.
    assert "Written after the delete" in [
        g["name"] for g in synced.http.get("/api/snapshot").json()["goals"]
    ]


def test_editing_something_the_other_device_deleted(synced):
    svc = synced.svc
    goal = svc.add_todo_task("Doomed", "", None)
    synced.engine.sync()
    goal_uid = synced.mirror.uid_for_id("todo_tasks", goal.id)

    synced.http.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "delete_goal",
        "params": {"goal_uid": goal_uid}}]})

    synced.client.offline = True
    svc.update_todo_task(goal.id, "Renamed", "", None)
    svc.toggle_goal_focused(goal.id)
    svc.add_todo_task("Later work", "", None)
    synced.engine.sync()
    synced.client.offline = False

    result = synced.engine.sync()
    assert result.ok
    assert state.pending_count(synced.mirror.connection) == 0
    assert "Later work" in [
        g["name"] for g in synced.http.get("/api/snapshot").json()["goals"]
    ]


def test_a_reorder_naming_a_deleted_row_still_drains(synced):
    svc = synced.svc
    first = svc.add_todo_task("First", "", None)
    second = svc.add_todo_task("Second", "", None)
    synced.engine.sync()
    first_uid = synced.mirror.uid_for_id("todo_tasks", first.id)

    synced.http.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "delete_goal",
        "params": {"goal_uid": first_uid}}]})

    synced.client.offline = True
    svc.set_todo_task_order([second.id, first.id], completed=False)
    synced.engine.sync()
    synced.client.offline = False

    assert synced.engine.sync().ok
    assert state.pending_count(synced.mirror.connection) == 0


def test_a_queued_start_that_arrives_late_does_not_jam(synced):
    """Two devices both start a timer; the loser must not block its own queue."""
    svc = synced.svc
    physics = svc.add_subject("Physics", "#3B82F6", "")
    maths = svc.add_subject("Maths", "#EF4444", "")
    synced.engine.sync()
    maths_uid = synced.mirror.uid_for_id("tasks", maths.id)

    synced.http.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "start_subject",
        "params": {"subject_uid": maths_uid}}]})

    synced.client.offline = True
    svc.start_subject(physics.id)
    svc.add_todo_task("Queued behind the start", "", None)
    synced.engine.sync()
    synced.client.offline = False

    result = synced.engine.sync()
    assert result.ok
    assert state.pending_count(synced.mirror.connection) == 0
    assert "Queued behind the start" in [
        g["name"] for g in synced.http.get("/api/snapshot").json()["goals"]
    ]


def test_a_genuinely_impossible_create_still_blocks(synced):
    """The queue must still stop for something that would lose data."""
    state.enqueue(synced.mirror.connection, "add_session", {
        "subject_uid": "does-not-exist",
        "start_time": "2026-06-01T10:00:00",
        "end_time": "2026-06-01T11:00:00",
    })
    result = synced.engine.sync()
    assert result.ok is False
    assert result.blocked_reason
    assert state.pending_count(synced.mirror.connection) == 1
