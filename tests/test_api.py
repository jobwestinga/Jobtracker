"""The server API: auth, idempotent operations, the change feed, and the rule
that local integer ids never leave the machine.

The server is just ``TrackerService`` over SQLite, so it runs in-process here —
no network, no running server, no fixtures pointing at real data.
"""

import json
import uuid
from datetime import date, timedelta

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from jobtracker.core import sync_policy  # noqa: E402
from server import app as app_module  # noqa: E402
from server import auth  # noqa: E402

TestClient = fastapi_testclient.TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live API bound to a throwaway database, with one enrolled device."""
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("test-device")

    state = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", state["db"])
    monkeypatch.setitem(app_module._state, "svc", state["svc"])

    client = TestClient(app_module.app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield client
    finally:
        state["db"].connection.close()


def do(client, op, params=None, op_id=None):
    """Send one operation and return its result payload."""
    body = {
        "ops": [
            {
                "op_id": op_id or str(uuid.uuid4()),
                "op": op,
                "params": params or {},
            }
        ]
    }
    response = client.post("/ops", json=body)
    return response


def result_of(response, index=0):
    return response.json()["applied"][index]["result"]


def make_subject(client, name="Physics"):
    return result_of(do(client, "add_subject", {"name": name}))["subject"]["uid"]


# ── auth ────────────────────────────────────────────────────────────────


def test_health_needs_no_token(api):
    api.headers.pop("Authorization")
    assert api.get("/health").status_code == 200


def test_requests_without_a_token_are_rejected(api):
    api.headers.pop("Authorization")
    assert api.get("/api/snapshot").status_code == 401
    assert api.post("/ops", json={"ops": []}).status_code in (401, 422)


def test_a_wrong_token_is_rejected(api):
    api.headers.update({"Authorization": "Bearer not-the-real-token"})
    assert api.get("/api/snapshot").status_code == 401


def test_a_revoked_token_stops_working(api, tmp_path, monkeypatch):
    assert api.get("/api/snapshot").status_code == 200
    auth.revoke_token("test-device")
    assert api.get("/api/snapshot").status_code == 401


def test_tokens_are_stored_hashed_never_in_the_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("macbook")
    stored = (tmp_path / "tokens.json").read_text()
    assert token not in stored
    assert auth.hash_token(token) in stored


# ── the identity invariant ──────────────────────────────────────────────


def _assert_no_integer_ids(payload, path="root"):
    """No response may carry a local row id, under any key ending in _id or 'id'.

    Two machines mint the same integer ids independently, so an id that escapes
    here is how they would eventually disagree about which row is which.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key != "id", f"integer id leaked at {path}"
            if key.endswith("_id") and key not in ("device_id", "op_id"):
                assert not isinstance(value, int), f"local id leaked at {path}.{key}"
            _assert_no_integer_ids(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            _assert_no_integer_ids(item, f"{path}[{i}]")


def test_no_endpoint_leaks_a_local_row_id(api):
    subject_uid = make_subject(api)
    goal = result_of(do(api, "add_goal", {"name": "Pass the exam"}))["goal"]
    do(api, "add_milestone", {"goal_uid": goal["uid"], "title": "Chapter 1"})
    do(
        api,
        "add_session",
        {
            "subject_uid": subject_uid,
            "start_time": "2026-06-01T10:00:00",
            "end_time": "2026-06-01T11:00:00",
        },
    )

    for path in (
        "/api/snapshot",
        "/api/active",
        "/sync/pull?since=0",
        "/sync/integrity",
        "/api/graphs/breakdown",
        "/api/graphs/heatmap",
        "/api/sessions/day/2026-06-01",
    ):
        response = api.get(path)
        assert response.status_code == 200, path
        _assert_no_integer_ids(response.json(), path)


def test_foreign_keys_travel_as_uids(api):
    subject_uid = make_subject(api)
    session = result_of(
        do(
            api,
            "add_session",
            {
                "subject_uid": subject_uid,
                "start_time": "2026-06-01T10:00:00",
                "end_time": "2026-06-01T11:00:00",
            },
        )
    )["session"]
    assert session["subject_uid"] == subject_uid
    assert "task_id" not in session


# ── idempotency: the anti-duplicate guarantee ───────────────────────────


def test_replaying_an_op_id_does_not_apply_it_twice(api):
    subject_uid = make_subject(api)
    op_id = str(uuid.uuid4())
    params = {
        "subject_uid": subject_uid,
        "start_time": "2026-06-01T10:00:00",
        "end_time": "2026-06-01T11:00:00",
    }

    first = result_of(do(api, "add_session", params, op_id=op_id))
    second = result_of(do(api, "add_session", params, op_id=op_id))

    assert first["session"]["uid"] == second["session"]["uid"]
    assert second["replayed"] is True
    assert len(api.get("/api/snapshot").json()["sessions"]) == 1


def test_a_retried_batch_after_a_timeout_creates_nothing_extra(api):
    """The exact offline-queue failure: client sends, connection dies before the
    response arrives, client retries the same queue."""
    subject_uid = make_subject(api)
    batch = {
        "ops": [
            {
                "op_id": "op-fixed-1",
                "op": "add_session",
                "params": {
                    "subject_uid": subject_uid,
                    "start_time": "2026-06-01T10:00:00",
                    "end_time": "2026-06-01T11:00:00",
                },
            },
            {"op_id": "op-fixed-2", "op": "add_goal", "params": {"name": "Read a book"}},
        ]
    }
    api.post("/ops", json=batch)
    api.post("/ops", json=batch)
    api.post("/ops", json=batch)

    snapshot = api.get("/api/snapshot").json()
    assert len(snapshot["sessions"]) == 1
    assert len(snapshot["goals"]) == 1


def test_a_failed_op_stops_the_batch_and_reports_where(api):
    subject_uid = make_subject(api)
    response = api.post(
        "/ops",
        json={
            "ops": [
                {"op_id": str(uuid.uuid4()), "op": "add_goal", "params": {"name": "First"}},
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "add_session",
                    "params": {
                        "subject_uid": "does-not-exist",
                        "start_time": "2026-06-01T10:00:00",
                        "end_time": "2026-06-01T11:00:00",
                    },
                },
                {"op_id": str(uuid.uuid4()), "op": "add_goal", "params": {"name": "Third"}},
            ]
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["failed_index"] == 1
    assert len(body["applied"]) == 1
    # The operation after the failure must NOT have been applied.
    names = {g["name"] for g in api.get("/api/snapshot").json()["goals"]}
    assert names == {"First"}


def test_unknown_operation_is_refused(api):
    assert do(api, "drop_everything", {}).status_code == 400


# ── business rules run on the server ────────────────────────────────────


def test_milestone_gate_is_enforced_server_side(api):
    goal = result_of(do(api, "add_goal", {"name": "Pass the exam"}))["goal"]
    do(api, "add_milestone", {"goal_uid": goal["uid"], "title": "Chapter 1"})

    refused = do(api, "complete_goal", {"goal_uid": goal["uid"]})
    assert refused.status_code == 409

    milestone = api.get("/api/snapshot").json()["milestones"][0]
    do(api, "set_milestone_done", {"milestone_uid": milestone["uid"], "done": True})
    assert do(api, "complete_goal", {"goal_uid": goal["uid"]}).status_code == 200


def test_device_local_settings_are_refused(api):
    assert do(api, "set_setting", {"key": "theme_fx", "value": "Glow"}).status_code == 400
    assert (
        do(api, "set_setting", {"key": "day_start_time", "value": "04:00"}).status_code
        == 200
    )


def test_starting_a_second_timer_is_refused(api):
    first = make_subject(api, "Physics")
    second = make_subject(api, "Maths")
    assert do(api, "start_subject", {"subject_uid": first}).status_code == 200
    assert do(api, "start_subject", {"subject_uid": second}).status_code == 409


def test_stopping_when_nothing_runs_is_a_no_op_not_an_error(api):
    """Two devices may both stop the same timer; the loser must not see a failure."""
    subject_uid = make_subject(api)
    do(api, "start_subject", {"subject_uid": subject_uid})
    assert result_of(do(api, "stop_active_subject"))["stopped"] is True
    assert result_of(do(api, "stop_active_subject"))["stopped"] is False


def test_active_timer_is_visible_to_every_client(api):
    subject_uid = make_subject(api)
    do(api, "start_subject", {"subject_uid": subject_uid})
    active = api.get("/api/active").json()
    assert active["active"] is not None
    assert active["subject_uid"] == subject_uid
    assert active["elapsed_seconds"] >= 0


# ── the change feed ─────────────────────────────────────────────────────


def test_feed_reports_creations_and_advances_the_cursor(api):
    start = api.get("/sync/pull?since=0").json()
    assert start["changes"] == [] or all(c["op"] == "upsert" for c in start["changes"])

    make_subject(api)
    after = api.get(f"/sync/pull?since={start['seq']}").json()

    tables = {c["table"] for c in after["changes"]}
    assert "subjects" in tables
    assert after["seq"] > start["seq"]


def test_feed_reports_a_delete_with_a_tombstone(api):
    subject_uid = make_subject(api)
    cursor = api.get("/sync/pull?since=0").json()["seq"]

    do(api, "delete_subject", {"subject_uid": subject_uid})
    changes = api.get(f"/sync/pull?since={cursor}").json()["changes"]

    deletes = [c for c in changes if c["op"] == "delete" and c["uid"] == subject_uid]
    assert deletes, "delete never reached the feed"
    assert "row" not in deletes[0]


def test_deleted_rows_are_archived_not_destroyed(api):
    subject_uid = make_subject(api, "Chemistry")
    do(api, "delete_subject", {"subject_uid": subject_uid})

    db = app_module._state["db"]
    row = db.connection.execute(
        "SELECT payload FROM deleted_rows WHERE row_uid = ?", (subject_uid,)
    ).fetchone()
    assert row is not None
    assert json.loads(row["payload"])["name"] == "Chemistry"


def test_feed_collapses_repeated_edits_to_one_current_row(api):
    subject_uid = make_subject(api)
    cursor = api.get("/sync/pull?since=0").json()["seq"]
    for name in ("A", "B", "C"):
        do(api, "update_subject", {"subject_uid": subject_uid, "name": name, "color": "#111111"})

    changes = api.get(f"/sync/pull?since={cursor}").json()["changes"]
    subject_changes = [c for c in changes if c["uid"] == subject_uid]
    assert len(subject_changes) == 1
    assert subject_changes[0]["row"]["name"] == "C"


def test_pull_since_head_is_empty(api):
    make_subject(api)
    head = api.get("/sync/pull?since=0").json()["seq"]
    assert api.get(f"/sync/pull?since={head}").json()["changes"] == []


# ── integrity check ─────────────────────────────────────────────────────


def test_integrity_hash_changes_when_data_changes(api):
    before = api.get("/sync/integrity").json()
    make_subject(api)
    after = api.get("/sync/integrity").json()

    assert before["tables"]["subjects"]["count"] == 0
    assert after["tables"]["subjects"]["count"] == 1
    assert before["tables"]["subjects"]["hash"] != after["tables"]["subjects"]["hash"]


def test_integrity_covers_every_synced_table(api):
    tables = api.get("/sync/integrity").json()["tables"]
    expected = {sync_policy.API_NAMES[t] for t in sync_policy.SYNCED_TABLES}
    assert set(tables) == expected


def test_snapshot_contains_every_table_and_the_head(api):
    make_subject(api)
    snapshot = api.get("/api/snapshot").json()
    for table in sync_policy.SYNCED_TABLES:
        assert sync_policy.API_NAMES[table] in snapshot
    assert isinstance(snapshot["head"], int)


# ── the phone app ───────────────────────────────────────────────────────


def test_context_reports_the_servers_logical_day(api):
    """The phone must not work out "today" itself: the logical day starts at
    03:00, and a second implementation is a second thing that can disagree."""
    body = api.get("/api/context").json()
    assert body["day_start"] == "03:00"
    assert len(body["today"]) == 10  # YYYY-MM-DD
    assert body["server_time"]


def test_context_follows_a_changed_day_start(api):
    do(api, "set_setting", {"key": "day_start_time", "value": "06:00"})
    assert api.get("/api/context").json()["day_start"] == "06:00"


def test_the_phone_app_is_served(api):
    for path, expected in (
        ("/", "text/html"),
        ("/app.js", "javascript"),
        ("/styles.css", "css"),
        ("/manifest.webmanifest", "json"),
    ):
        response = api.get(path)
        assert response.status_code == 200, path
        assert expected in response.headers.get("content-type", ""), path


def test_static_files_do_not_require_a_token(api):
    """The page has to load before it can ask for a token."""
    api.headers.pop("Authorization")
    assert api.get("/").status_code == 200
    assert api.get("/app.js").status_code == 200
    # The data behind it still does.
    assert api.get("/api/snapshot").status_code == 401


def test_mounting_the_app_did_not_shadow_the_api(api):
    """A mount at "/" swallows every route registered after it."""
    for path in ("/health", "/api/context", "/api/snapshot", "/sync/integrity", "/ops/known"):
        assert api.get(path).status_code == 200, path


def test_agenda_places_sessions_at_their_clock_position(api):
    subject_uid = make_subject(api)
    today = api.get("/api/context").json()["today"]
    do(api, "add_session", {
        "subject_uid": subject_uid,
        "start_time": f"{today}T10:00:00",
        "end_time": f"{today}T12:30:00",
    })
    body = api.get("/api/graphs/agenda?days=3").json()

    assert today in body["days"]
    assert body["day_start_hour"] == 3.0
    placed = [s for s in body["sessions"] if s["day"] == today]
    assert placed and placed[0]["start_h"] == 10.0 and placed[0]["end_h"] == 12.5
    assert placed[0]["subject_uid"] == subject_uid


def test_after_midnight_work_belongs_to_the_previous_day(api):
    """01:00 is hour 25 of yesterday, not hour 1 of today — the same rule the
    desktop agenda uses, so the two cannot disagree."""
    subject_uid = make_subject(api)
    today = api.get("/api/context").json()["today"]
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    do(api, "add_session", {
        "subject_uid": subject_uid,
        "start_time": f"{tomorrow}T01:00:00",
        "end_time": f"{tomorrow}T02:00:00",
    })
    body = api.get("/api/graphs/agenda?days=3").json()

    placed = [s for s in body["sessions"] if s["start_h"] >= 24]
    assert placed, "after-midnight session was not mapped past hour 24"
    assert placed[0]["day"] == today
    assert placed[0]["start_h"] == 25.0
