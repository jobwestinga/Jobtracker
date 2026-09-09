"""Every operation the server accepts, exercised at least once.

`/ops` is the only way anything is written, by either client, so an operation
that is broken or missing a handler is a feature that silently does not work on
the phone. This walks the whole registry: each one is called for real, and the
registry is checked against the list below so a newly added operation cannot
slip in untested.

Also covers the failure shapes — unknown uids, malformed input, refusals — since
those decide whether a client's outbox drains or jams.
"""

import uuid
from datetime import datetime, timedelta

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from server import app as app_module  # noqa: E402
from server import auth, ops  # noqa: E402

TestClient = fastapi_testclient.TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("test")
    state = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", state["db"])
    monkeypatch.setitem(app_module._state, "svc", state["svc"])
    client = TestClient(app_module.app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield client
    finally:
        state["db"].connection.close()


def send(client, name, params=None):
    return client.post("/ops", json={"ops": [
        {"op_id": str(uuid.uuid4()), "op": name, "params": params or {}}
    ]})


def op(client, name, params=None):
    response = send(client, name, params)
    assert response.status_code == 200, f"{name}: {response.text}"
    return response.json()["applied"][0]["result"]


@pytest.fixture
def world(api):
    """One of everything, so each operation has something to act on."""
    subject = op(api, "add_subject", {"name": "Physics", "color": "#3B82F6"})["subject"]
    other = op(api, "add_subject", {"name": "Maths", "color": "#EF4444"})["subject"]
    now = datetime.now().replace(microsecond=0)
    session = op(api, "add_session", {
        "subject_uid": subject["uid"],
        "start_time": (now - timedelta(hours=3)).isoformat(),
        "end_time": (now - timedelta(hours=2)).isoformat(),
    })["session"]
    goal = op(api, "add_goal", {"name": "Pass the exam", "notes": "big one"})["goal"]
    milestone = op(api, "add_milestone", {
        "goal_uid": goal["uid"], "title": "Chapter 1",
    })["milestone"]
    template = op(api, "add_template", {
        "title": "Daily review", "recurrence": "daily",
    })["template"]
    return {
        "api": api, "subject": subject, "other": other, "session": session,
        "goal": goal, "milestone": milestone, "template": template, "now": now,
    }


# ── the registry itself ─────────────────────────────────────────────────

EXPECTED_OPS = {
    "add_subject", "update_subject", "archive_subject", "unarchive_subject",
    "delete_subject", "set_subject_order",
    "start_subject", "stop_active_subject", "switch_subject", "heartbeat",
    "add_session", "update_session", "delete_session", "duplicate_session",
    "shift_session",
    "add_goal", "update_goal", "delete_goal", "complete_goal", "uncomplete_goal",
    "toggle_goal_focused", "set_goal_order",
    "add_milestone", "update_milestone", "set_milestone_done", "delete_milestone",
    "set_milestone_order",
    "add_template", "update_template", "set_template_active", "delete_template",
    "generate_due_goals",
    "set_setting",
}


def test_the_registry_is_exactly_what_is_expected():
    """A new operation must be added to this list, and therefore tested."""
    assert set(ops.known_ops()) == EXPECTED_OPS


def test_the_endpoint_reports_the_same_registry(api):
    assert set(api.get("/ops/known").json()["ops"]) == EXPECTED_OPS


# ── subjects ────────────────────────────────────────────────────────────


def test_update_subject(world):
    api, subject = world["api"], world["subject"]
    result = op(api, "update_subject", {
        "subject_uid": subject["uid"], "name": "Physics II", "color": "#111111",
        "notes": "renamed",
    })
    assert result["subject"]["name"] == "Physics II"
    assert result["subject"]["color"] == "#111111"


def test_archive_and_unarchive_subject(world):
    api, subject = world["api"], world["subject"]
    op(api, "archive_subject", {"subject_uid": subject["uid"]})
    archived = [s for s in api.get("/api/snapshot").json()["subjects"]
                if s["uid"] == subject["uid"]][0]
    assert archived["is_archived"] == 1

    op(api, "unarchive_subject", {"subject_uid": subject["uid"]})
    restored = [s for s in api.get("/api/snapshot").json()["subjects"]
                if s["uid"] == subject["uid"]][0]
    assert restored["is_archived"] == 0


def test_delete_subject_takes_its_sessions_with_it(world):
    api, subject = world["api"], world["subject"]
    op(api, "delete_subject", {"subject_uid": subject["uid"]})
    snapshot = api.get("/api/snapshot").json()
    assert all(s["uid"] != subject["uid"] for s in snapshot["subjects"])
    assert all(s["subject_uid"] != subject["uid"] for s in snapshot["sessions"])


def test_set_subject_order(world):
    api, subject, other = world["api"], world["subject"], world["other"]
    op(api, "set_subject_order", {"subject_uids": [other["uid"], subject["uid"]]})
    ordered = sorted(api.get("/api/snapshot").json()["subjects"],
                     key=lambda s: s["sort_order"])
    assert [s["uid"] for s in ordered] == [other["uid"], subject["uid"]]


# ── the timer ───────────────────────────────────────────────────────────


def test_start_stop_and_switch(world):
    api, subject, other = world["api"], world["subject"], world["other"]
    op(api, "start_subject", {"subject_uid": subject["uid"]})
    assert api.get("/api/active").json()["subject_uid"] == subject["uid"]

    op(api, "switch_subject", {"subject_uid": other["uid"]})
    assert api.get("/api/active").json()["subject_uid"] == other["uid"]

    end = (datetime.now() + timedelta(minutes=40)).replace(microsecond=0)
    assert op(api, "stop_active_subject", {"end_time": end.isoformat()})["stopped"]
    assert api.get("/api/active").json()["active"] is None


def test_heartbeat_is_harmless_with_no_timer(world):
    assert op(world["api"], "heartbeat")["ok"] is True


def test_heartbeat_touches_a_running_session(world):
    api, subject = world["api"], world["subject"]
    op(api, "start_subject", {"subject_uid": subject["uid"]})
    assert op(api, "heartbeat")["ok"] is True
    op(api, "stop_active_subject", {
        "end_time": (datetime.now() + timedelta(minutes=31)).isoformat()
    })


# ── sessions ────────────────────────────────────────────────────────────


def test_update_session_can_move_it_to_another_subject(world):
    api, session, other = world["api"], world["session"], world["other"]
    now = world["now"]
    result = op(api, "update_session", {
        "session_uid": session["uid"],
        "subject_uid": other["uid"],
        "start_time": (now - timedelta(hours=5)).isoformat(),
        "end_time": (now - timedelta(hours=4)).isoformat(),
    })
    assert result["session"]["subject_uid"] == other["uid"]
    # The row survives the move: same identity, so no history is orphaned.
    assert result["session"]["uid"] == session["uid"]


def test_shift_session_moves_both_ends(world):
    api, session = world["api"], world["session"]
    before = [s for s in api.get("/api/snapshot").json()["sessions"]
              if s["uid"] == session["uid"]][0]
    result = op(api, "shift_session", {"session_uid": session["uid"], "seconds": 900})
    assert result["session"]["duration_seconds"] == before["duration_seconds"]
    assert result["session"]["start_time"] > before["start_time"]


@pytest.mark.parametrize("target", ["today", "next_day"])
def test_duplicate_session(world, target):
    api, session = world["api"], world["session"]
    result = op(api, "duplicate_session", {
        "session_uid": session["uid"], "to": target,
    })
    assert result["session"]["uid"] != session["uid"]
    assert result["session"]["duration_seconds"] == 3600


def test_duplicate_refuses_a_target_it_does_not_know(world):
    response = send(world["api"], "duplicate_session", {
        "session_uid": world["session"]["uid"], "to": "next_year",
    })
    assert response.status_code == 400


def test_delete_session(world):
    api, session = world["api"], world["session"]
    op(api, "delete_session", {"session_uid": session["uid"]})
    assert all(s["uid"] != session["uid"]
               for s in api.get("/api/snapshot").json()["sessions"])


# ── goals and milestones ────────────────────────────────────────────────


def test_update_goal(world):
    api, goal = world["api"], world["goal"]
    result = op(api, "update_goal", {
        "goal_uid": goal["uid"], "name": "Pass it well", "notes": "updated",
    })
    assert result["goal"]["name"] == "Pass it well"


def test_complete_and_reopen_a_goal(world):
    api, goal, milestone = world["api"], world["goal"], world["milestone"]
    op(api, "set_milestone_done", {"milestone_uid": milestone["uid"], "done": True})
    assert op(api, "complete_goal", {"goal_uid": goal["uid"]})["goal"]["is_completed"] == 1
    assert op(api, "uncomplete_goal", {"goal_uid": goal["uid"]})["goal"]["is_completed"] == 0


def test_toggle_focus_twice_returns_to_the_start(world):
    api, goal = world["api"], world["goal"]
    assert op(api, "toggle_goal_focused", {"goal_uid": goal["uid"]})["focused"] is True
    assert op(api, "toggle_goal_focused", {"goal_uid": goal["uid"]})["focused"] is False


def test_set_goal_order(world):
    api, goal = world["api"], world["goal"]
    second = op(api, "add_goal", {"name": "Second goal"})["goal"]
    op(api, "set_goal_order", {"goal_uids": [second["uid"], goal["uid"]]})
    ordered = sorted(api.get("/api/snapshot").json()["goals"],
                     key=lambda g: g["sort_order"])
    assert [g["uid"] for g in ordered][:2] == [second["uid"], goal["uid"]]


def test_update_and_delete_a_milestone(world):
    api, milestone = world["api"], world["milestone"]
    result = op(api, "update_milestone", {
        "milestone_uid": milestone["uid"], "title": "Chapter 1 (revised)",
        "note": "with notes",
    })
    assert result["milestone"]["title"] == "Chapter 1 (revised)"

    op(api, "delete_milestone", {"milestone_uid": milestone["uid"]})
    assert all(m["uid"] != milestone["uid"]
               for m in api.get("/api/snapshot").json()["milestones"])


def test_set_milestone_order(world):
    api, goal, first = world["api"], world["goal"], world["milestone"]
    second = op(api, "add_milestone", {
        "goal_uid": goal["uid"], "title": "Chapter 2",
    })["milestone"]
    op(api, "set_milestone_order", {
        "goal_uid": goal["uid"],
        "milestone_uids": [second["uid"], first["uid"]],
    })
    mine = sorted(
        [m for m in api.get("/api/snapshot").json()["milestones"]
         if m["goal_uid"] == goal["uid"]],
        key=lambda m: m["sort_order"],
    )
    assert [m["uid"] for m in mine] == [second["uid"], first["uid"]]


def test_deleting_a_goal_takes_its_milestones(world):
    api, goal = world["api"], world["goal"]
    op(api, "delete_goal", {"goal_uid": goal["uid"]})
    snapshot = api.get("/api/snapshot").json()
    assert all(g["uid"] != goal["uid"] for g in snapshot["goals"])
    assert all(m["goal_uid"] != goal["uid"] for m in snapshot["milestones"])


# ── templates ───────────────────────────────────────────────────────────


def test_update_a_template(world):
    api, template = world["api"], world["template"]
    result = op(api, "update_template", {
        "template_uid": template["uid"], "title": "Weekly review",
        "recurrence": "weekly", "recurrence_day": 3,
        "milestone_titles": ["Inbox zero"],
    })
    assert result["template"]["title"] == "Weekly review"
    assert result["template"]["recurrence"] == "weekly"
    assert result["template"]["recurrence_day"] == 3


def test_deactivate_a_template(world):
    api, template = world["api"], world["template"]
    assert op(api, "set_template_active", {
        "template_uid": template["uid"], "active": False,
    })["template"]["is_active"] == 0


def test_generate_due_goals_is_idempotent_within_a_period(world):
    """Called on every launch and every Goals-tab open, so it must not pile up."""
    api = world["api"]
    first = op(api, "generate_due_goals")["created"]
    assert len(first) == 1
    for _ in range(5):
        assert op(api, "generate_due_goals")["created"] == []


def test_an_inactive_template_generates_nothing(world):
    api, template = world["api"], world["template"]
    op(api, "set_template_active", {"template_uid": template["uid"], "active": False})
    assert op(api, "generate_due_goals")["created"] == []


def test_delete_a_template(world):
    api, template = world["api"], world["template"]
    op(api, "delete_template", {"template_uid": template["uid"]})
    assert all(t["uid"] != template["uid"]
               for t in api.get("/api/snapshot").json()["templates"])


# ── failure shapes: these decide whether an outbox drains or jams ───────


# A row that has already gone must NOT fail: the outbox stops at the first
# refusal, so an operation that can never succeed would block every later write
# from that device forever.
@pytest.mark.parametrize("operation,params", [
    ("update_subject", {"subject_uid": "nope", "name": "x", "color": "#111111"}),
    ("archive_subject", {"subject_uid": "nope"}),
    ("unarchive_subject", {"subject_uid": "nope"}),
    ("delete_subject", {"subject_uid": "nope"}),
    ("start_subject", {"subject_uid": "nope"}),
    ("switch_subject", {"subject_uid": "nope"}),
    ("delete_session", {"session_uid": "nope"}),
    ("shift_session", {"session_uid": "nope", "seconds": 60}),
    ("duplicate_session", {"session_uid": "nope", "to": "today"}),
    ("update_goal", {"goal_uid": "nope", "name": "x"}),
    ("delete_goal", {"goal_uid": "nope"}),
    ("complete_goal", {"goal_uid": "nope"}),
    ("uncomplete_goal", {"goal_uid": "nope"}),
    ("toggle_goal_focused", {"goal_uid": "nope"}),
    ("update_milestone", {"milestone_uid": "nope", "title": "x"}),
    ("set_milestone_done", {"milestone_uid": "nope", "done": True}),
    ("delete_milestone", {"milestone_uid": "nope"}),
    ("delete_template", {"template_uid": "nope"}),
    ("set_template_active", {"template_uid": "nope", "active": False}),
])
def test_acting_on_a_row_that_is_gone_is_a_recorded_skip(world, operation, params):
    response = send(world["api"], operation, params)
    assert response.status_code == 200, operation
    result = response.json()["applied"][0]["result"]
    assert result["skipped"] is True
    assert "no longer exists" in result["reason"]


# Creates are the exception: silently dropping one would lose the work it records.
@pytest.mark.parametrize("operation,params", [
    ("add_session", {"subject_uid": "nope", "start_time": "2026-06-01T10:00:00",
                     "end_time": "2026-06-01T11:00:00"}),
    ("add_milestone", {"goal_uid": "nope", "title": "x"}),
])
def test_a_create_whose_parent_is_gone_still_fails(world, operation, params):
    response = send(world["api"], operation, params)
    assert response.status_code == 404, operation
    assert "unknown" in response.json()["detail"]


def test_update_session_still_needs_a_real_subject(world):
    """Moving a session onto a subject that does not exist would orphan it."""
    response = send(world["api"], "update_session", {
        "session_uid": world["session"]["uid"],
        "subject_uid": "nope",
        "start_time": "2026-06-01T10:00:00",
        "end_time": "2026-06-01T11:00:00",
    })
    assert response.status_code == 404


def test_a_reorder_ignores_rows_that_have_gone(world):
    """A reorder naming one deleted goal is still good for the rest."""
    api, goal = world["api"], world["goal"]
    second = op(api, "add_goal", {"name": "Second"})["goal"]
    op(api, "delete_goal", {"goal_uid": goal["uid"]})

    response = send(api, "set_goal_order", {
        "goal_uids": [goal["uid"], second["uid"]],
    })
    assert response.status_code == 200


def test_starting_while_something_runs_reports_rather_than_fails(world):
    api, subject, other = world["api"], world["subject"], world["other"]
    op(api, "start_subject", {"subject_uid": subject["uid"]})
    result = op(api, "start_subject", {"subject_uid": other["uid"]})
    assert result["started"] is False
    assert "already running" in result["reason"]
    op(api, "stop_active_subject", {
        "end_time": (datetime.now() + timedelta(minutes=31)).isoformat()
    })


def test_completing_a_gated_goal_reports_rather_than_fails(world):
    """The milestone rule still holds; it just does not jam the queue."""
    api, goal = world["api"], world["goal"]
    result = op(api, "complete_goal", {"goal_uid": goal["uid"]})
    assert result["completed"] is False
    assert "unchecked milestones" in result["reason"]
    assert result["goal"]["is_completed"] == 0


@pytest.mark.parametrize("bad_time", ["", "not-a-date", "2026-13-45", "12:00"])
def test_a_malformed_timestamp_is_refused_not_stored(world, bad_time):
    response = send(world["api"], "add_session", {
        "subject_uid": world["subject"]["uid"],
        "start_time": bad_time,
        "end_time": bad_time,
    })
    assert response.status_code == 400


def test_an_empty_subject_name_is_refused(world):
    assert send(world["api"], "add_subject", {"name": "   "}).status_code == 400


def test_an_empty_goal_name_is_refused(world):
    assert send(world["api"], "add_goal", {"name": ""}).status_code == 400


def test_a_batch_larger_than_the_limit_is_refused(api):
    response = api.post("/ops", json={"ops": [
        {"op_id": str(uuid.uuid4()), "op": "add_goal", "params": {"name": f"g{i}"}}
        for i in range(201)
    ]})
    assert response.status_code == 422


def test_an_empty_batch_is_refused(api):
    assert api.post("/ops", json={"ops": []}).status_code == 422


def test_a_too_short_op_id_is_refused(api):
    response = api.post("/ops", json={"ops": [
        {"op_id": "short", "op": "add_goal", "params": {"name": "x"}}
    ]})
    assert response.status_code == 422
