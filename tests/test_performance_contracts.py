"""The promises the optimisations rest on.

These are not benchmarks — timings are too flaky to assert on. They pin the
*contracts* that make the app cheap: the change counter really does stay put
when nothing happens, the cheap integrity check really is cheap, responses are
compressed, and the queries behind the graphs do not degrade into scanning
everything.

If one of these breaks, the app still works — it just quietly gets slower or
chattier, which is exactly the kind of regression nothing else would catch.
"""

import uuid
from datetime import datetime, timedelta

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from jobtracker.core import sync_policy  # noqa: E402
from jobtracker.core.database import Database  # noqa: E402
from jobtracker.services.tracker_service import TrackerService  # noqa: E402
from jobtracker.sync import state  # noqa: E402
from jobtracker.sync.engine import SyncEngine, SyncResult  # noqa: E402
from server import app as app_module  # noqa: E402
from server import auth  # noqa: E402
from tests.test_sync_engine import InProcessClient  # noqa: E402

TestClient = fastapi_testclient.TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("perf")
    state_ = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", state_["db"])
    monkeypatch.setitem(app_module._state, "svc", state_["svc"])
    client = TestClient(app_module.app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield client
    finally:
        state_["db"].connection.close()


def op(client, name, params=None):
    response = client.post("/ops", json={"ops": [
        {"op_id": str(uuid.uuid4()), "op": name, "params": params or {}}
    ]})
    assert response.status_code == 200, response.text
    return response.json()["applied"][0]["result"]


def a_subject(client, name="Physics"):
    return op(client, "add_subject", {"name": name})["subject"]["uid"]


# ── the change counter: the whole point of the cheap poll ───────────────


def test_context_carries_the_change_counter(api):
    body = api.get("/api/context").json()
    assert isinstance(body["head"], int)
    assert "active" in body


def test_the_counter_does_not_move_when_nothing_happens(api):
    """If this drifts on its own, every client re-downloads everything, forever."""
    first = api.get("/api/context").json()["head"]
    for _ in range(5):
        api.get("/api/context")
        api.get("/api/snapshot")
        api.get("/api/graphs/breakdown")
    assert api.get("/api/context").json()["head"] == first


def test_the_counter_moves_on_every_kind_of_write(api):
    subject_uid = a_subject(api)
    seen = api.get("/api/context").json()["head"]

    goal = op(api, "add_goal", {"name": "A goal"})["goal"]
    after_create = api.get("/api/context").json()["head"]
    assert after_create > seen

    op(api, "update_goal", {"goal_uid": goal["uid"], "name": "Renamed", "notes": ""})
    after_update = api.get("/api/context").json()["head"]
    assert after_update > after_create

    op(api, "delete_goal", {"goal_uid": goal["uid"]})
    assert api.get("/api/context").json()["head"] > after_update


def test_a_replayed_operation_does_not_move_the_counter(api):
    """A retry must not make every client re-download."""
    subject_uid = a_subject(api)
    body = {"ops": [{"op_id": "stable-op-id", "op": "add_goal",
                     "params": {"name": "Once"}}]}
    api.post("/ops", json=body)
    after_first = api.get("/api/context").json()["head"]
    api.post("/ops", json=body)
    api.post("/ops", json=body)
    assert api.get("/api/context").json()["head"] == after_first


def test_context_reports_the_running_timer_without_a_second_request(api):
    subject_uid = a_subject(api)
    op(api, "start_subject", {"subject_uid": subject_uid})

    body = api.get("/api/context").json()
    assert body["active"] is not None
    assert body["active"]["subject_uid"] == subject_uid
    assert body["active"]["elapsed_seconds"] >= 0

    op(api, "stop_active_subject", {"end_time": datetime.now().isoformat()})
    assert api.get("/api/context").json()["active"] is None


def test_context_stays_small_even_with_a_lot_of_history(api):
    """It is polled every minute; it must not grow with the database."""
    subject_uid = a_subject(api)
    base = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for offset in range(50):
        start = base - timedelta(days=offset)
        op(api, "add_session", {
            "subject_uid": subject_uid,
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(),
        })

    context = api.get("/api/context")
    snapshot = api.get("/api/snapshot")
    assert len(context.content) < 400
    # The point of the whole exercise: context is orders of magnitude smaller.
    assert len(context.content) * 20 < len(snapshot.content)


# ── compression ─────────────────────────────────────────────────────────


def test_large_responses_are_compressed(api):
    subject_uid = a_subject(api)
    base = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for offset in range(60):
        start = base - timedelta(days=offset)
        op(api, "add_session", {
            "subject_uid": subject_uid,
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(),
        })

    plain = api.get("/api/snapshot", headers={"Accept-Encoding": "identity"})
    zipped = api.get("/api/snapshot", headers={"Accept-Encoding": "gzip"})

    assert zipped.headers.get("content-encoding") == "gzip"
    # Same data either way; the client sees no difference.
    assert zipped.json() == plain.json()


def test_tiny_responses_are_not_compressed(api):
    """Compressing eighty bytes costs more than it saves."""
    response = api.get("/api/context", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") != "gzip"


# ── the integrity check, cheap and deep ─────────────────────────────────


def test_the_cheap_check_returns_counts_only(api):
    a_subject(api)
    body = api.get("/sync/integrity").json()
    assert body["deep"] is False
    for table in body["tables"].values():
        assert "count" in table
        assert "hash" not in table


def test_the_deep_check_adds_hashes(api):
    a_subject(api)
    body = api.get("/sync/integrity?deep=1").json()
    assert body["deep"] is True
    for table in body["tables"].values():
        assert table["hash"]


def test_a_count_mismatch_is_caught_by_the_cheap_check(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "t.json"))
    token = auth.issue_token("macbook")
    server = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server["db"])
    monkeypatch.setitem(app_module._state, "svc", server["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})
    mirror = Database(tmp_path / "mirror.db")
    engine = SyncEngine(mirror, InProcessClient(http, token), "macbook")
    try:
        a_subject(http)
        engine.sync()
        mirror.connection.execute("DELETE FROM tasks")
        mirror.connection.commit()

        result = SyncResult()
        assert engine.verify(result, deep=False) is False
        assert any("mirror has 0" in m for m in result.messages)
    finally:
        mirror.connection.close()
        server["db"].connection.close()


def test_the_right_number_of_wrong_rows_needs_the_deep_check(tmp_path, monkeypatch):
    """Counts alone cannot see this; the uid hash is what catches it."""
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "t.json"))
    token = auth.issue_token("macbook")
    server = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server["db"])
    monkeypatch.setitem(app_module._state, "svc", server["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})
    mirror = Database(tmp_path / "mirror.db")
    engine = SyncEngine(mirror, InProcessClient(http, token), "macbook")
    try:
        a_subject(http, "Physics")
        engine.sync()
        # Same number of rows, different identity.
        mirror.connection.execute(
            "UPDATE tasks SET uid = ?", (sync_policy.new_uid(),)
        )
        mirror.connection.commit()

        shallow = SyncResult()
        assert engine.verify(shallow, deep=False) is True   # counts still match

        deep = SyncResult()
        assert engine.verify(deep, deep=True) is False
        assert any("different rows" in m for m in deep.messages)
    finally:
        mirror.connection.close()
        server["db"].connection.close()


def test_the_deep_check_runs_periodically_not_every_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "t.json"))
    token = auth.issue_token("macbook")
    server = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server["db"])
    monkeypatch.setitem(app_module._state, "svc", server["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})
    mirror = Database(tmp_path / "mirror.db")
    engine = SyncEngine(mirror, InProcessClient(http, token), "macbook")
    try:
        deep_calls = []
        real = engine.client.integrity

        def counting(deep=False):
            deep_calls.append(deep)
            return real(deep=deep)

        engine.client.integrity = counting
        for _ in range(10):
            engine.sync()

        assert len(deep_calls) == 10
        assert deep_calls.count(True) == 1, "deep check should be occasional"
        assert deep_calls.count(False) == 9
    finally:
        mirror.connection.close()
        server["db"].connection.close()


# ── query shape behind the graphs ───────────────────────────────────────


def test_the_session_indexes_exist(database):
    """The graph queries filter on start time and on subject+start."""
    names = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_sessions_start_time" in names
    assert "idx_sessions_task_start" in names


def test_uid_lookups_are_indexed(database):
    """uid -> local id happens for every row of every sync; it cannot be a scan."""
    for table in sync_policy.SYNCED_TABLES:
        plan = database.connection.execute(
            f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE uid = 'x'"
        ).fetchall()
        text = " ".join(str(row[3]) for row in plan)
        # SQLite says "USING INDEX" or "USING COVERING INDEX"; either is fine,
        # a SCAN is not.
        assert "INDEX" in text and "SCAN" not in text, f"{table}: {text}"


def test_the_range_query_uses_the_start_time_index(database, service):
    subject = service.add_subject("Physics", "#3B82F6", "")
    now = datetime.now()
    service.add_session(subject.id, now - timedelta(hours=2), now - timedelta(hours=1))

    plan = database.connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM sessions "
        "WHERE start_time >= ? AND start_time <= ? AND end_time IS NOT NULL",
        ("2020-01-01T00:00:00", "2030-01-01T00:00:00"),
    ).fetchall()
    assert "idx_sessions_start_time" in " ".join(str(r[3]) for r in plan)


def test_wal_is_on_so_the_sync_thread_does_not_block_the_ui(database):
    mode = database.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_a_busy_timeout_is_set(database):
    timeout = database.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(timeout) >= 1000


def test_stats_for_every_subject_come_from_one_query(service):
    """get_subject_stats_map exists so the subject list is not N queries.

    It is built from a GROUP BY over sessions, so it only names subjects that
    have tracked something — a subject with no time is simply absent, and the
    card shows zero.
    """
    now = datetime.now()
    tracked = []
    for index in range(12):
        subject = service.add_subject(f"Subject {index}", "#3B82F6", "")
        if index % 2 == 0:
            service.add_session(
                subject.id, now - timedelta(hours=2), now - timedelta(hours=1)
            )
            tracked.append(subject.id)

    stats = service.get_subject_stats_map()
    assert set(stats) == set(tracked)
    assert all(seconds == 3600 for seconds in stats.values())


def test_a_quiet_sync_transfers_nothing_new(tmp_path, monkeypatch):
    """Two syncs in a row with no activity must apply nothing the second time."""
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "t.json"))
    token = auth.issue_token("macbook")
    server = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server["db"])
    monkeypatch.setitem(app_module._state, "svc", server["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})
    mirror = Database(tmp_path / "mirror.db")
    engine = SyncEngine(mirror, InProcessClient(http, token), "macbook")
    try:
        subject_uid = a_subject(http)
        for hour in range(9, 15):
            op(http, "add_session", {
                "subject_uid": subject_uid,
                "start_time": f"2026-06-01T{hour:02d}:00:00",
                "end_time": f"2026-06-01T{hour + 1:02d}:00:00",
            })
        first = engine.sync()
        assert first.applied > 0

        for _ in range(3):
            quiet = engine.sync()
            assert quiet.applied == 0
            assert quiet.deleted == 0
            assert quiet.repaired is False
    finally:
        mirror.connection.close()
        server["db"].connection.close()
