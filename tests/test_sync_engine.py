"""The desktop mirror and its outbox, against a real in-process server.

The "client" is a genuine ``Database`` and the "server" is the genuine FastAPI
app over another ``Database``; only the HTTP hop is replaced, so these exercise
the same code paths that run against the real server.

What is being proven: the mirror always converges to the server, offline work is
never lost, a retry never duplicates, and any drift repairs itself.
"""

import uuid

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from jobtracker.core import sync_policy  # noqa: E402
from jobtracker.core.database import Database  # noqa: E402
from jobtracker.services.tracker_service import TrackerService  # noqa: E402
from jobtracker.sync import state  # noqa: E402
from jobtracker.sync.client import SyncError  # noqa: E402
from jobtracker.sync.engine import SyncEngine  # noqa: E402
from server import app as app_module  # noqa: E402
from server import auth  # noqa: E402

TestClient = fastapi_testclient.TestClient


class InProcessClient:
    """Speaks the SyncClient interface but calls the app directly.

    ``offline`` flips the whole transport off, which is how the offline tests
    reproduce a dropped connection without any networking.
    """

    def __init__(self, http, token):
        self.http = http
        self.token = token
        self.offline = False
        self.sent_batches = []

    def _guard(self):
        if self.offline:
            raise SyncError("cannot reach server: simulated outage")

    def _check(self, response):
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            raise SyncError(str(detail), status=response.status_code, body=response.json())
        return response.json()

    def health(self):
        self._guard()
        return self._check(self.http.get("/health"))

    def snapshot(self):
        self._guard()
        return self._check(self.http.get("/api/snapshot"))

    def pull(self, since, limit=5000):
        self._guard()
        return self._check(self.http.get(f"/sync/pull?since={since}&limit={limit}"))

    def integrity(self, deep=False):
        self._guard()
        return self._check(
            self.http.get(f"/sync/integrity?deep={'1' if deep else '0'}")
        )

    def active(self):
        self._guard()
        return self._check(self.http.get("/api/active"))

    def send_ops(self, ops, device_id=""):
        self._guard()
        payload = {
            "ops": [
                {
                    "op_id": o["op_id"],
                    "op": o["op"],
                    "params": o["params"],
                    "device_id": device_id or None,
                }
                for o in ops
            ]
        }
        self.sent_batches.append(payload)
        return self._check(self.http.post("/ops", json=payload))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A server, and a desktop mirror pointed at it."""
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("macbook")

    server_state = app_module.build_state(str(tmp_path / "server.db"))
    monkeypatch.setitem(app_module._state, "db", server_state["db"])
    monkeypatch.setitem(app_module._state, "svc", server_state["svc"])
    http = TestClient(app_module.app)
    http.headers.update({"Authorization": f"Bearer {token}"})

    mirror = Database(tmp_path / "mirror.db")
    client = InProcessClient(http, token)
    engine = SyncEngine(mirror, client, device_id="macbook")

    class World:
        pass

    w = World()
    w.server_db = server_state["db"]
    w.server_svc = server_state["svc"]
    w.http = http
    w.mirror = mirror
    w.client = client
    w.engine = engine
    try:
        yield w
    finally:
        mirror.connection.close()
        server_state["db"].connection.close()


def server_add_subject(w, name="Physics"):
    response = w.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "add_subject",
                    "params": {"name": name, "color": "#3B82F6"},
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["applied"][0]["result"]["subject"]["uid"]


def mirror_names(w, table="tasks"):
    cur = w.mirror.connection.cursor()
    column = "title" if table == "goal_templates" else "name"
    cur.execute(f"SELECT {column} FROM {table} ORDER BY {column}")
    return [r[0] for r in cur.fetchall()]


def counts(db):
    cur = db.connection.cursor()
    out = {}
    for table in sync_policy.SYNCED_TABLES:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        out[table] = int(cur.fetchone()["n"])
    return out


# ── pulling ─────────────────────────────────────────────────────────────


def test_pull_brings_server_rows_into_the_mirror(world):
    server_add_subject(world, "Physics")
    server_add_subject(world, "Maths")

    result = world.engine.sync()

    assert result.ok
    assert mirror_names(world) == ["Maths", "Physics"]


def test_pull_is_incremental_not_a_full_download_each_time(world):
    server_add_subject(world, "Physics")
    world.engine.sync()

    first_cursor = state.last_seq(world.mirror.connection)
    second = world.engine.sync()

    assert second.applied == 0
    assert state.last_seq(world.mirror.connection) == first_cursor


def test_pull_applies_a_delete(world):
    uid = server_add_subject(world, "Physics")
    world.engine.sync()
    assert mirror_names(world) == ["Physics"]

    world.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "delete_subject",
                    "params": {"subject_uid": uid},
                }
            ]
        },
    )
    result = world.engine.sync()

    assert result.deleted == 1
    assert mirror_names(world) == []


def test_foreign_keys_are_rebuilt_as_local_ids(world):
    """Sessions arrive naming a subject uid; the mirror must relink them to its
    own integer ids or every existing query breaks."""
    subject_uid = server_add_subject(world, "Physics")
    world.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "add_session",
                    "params": {
                        "subject_uid": subject_uid,
                        "start_time": "2026-06-01T10:00:00",
                        "end_time": "2026-06-01T11:30:00",
                    },
                }
            ]
        },
    )
    world.engine.sync()

    cur = world.mirror.connection.cursor()
    row = cur.execute(
        "SELECT s.task_id, s.duration_seconds, t.name FROM sessions s "
        "JOIN tasks t ON t.id = s.task_id"
    ).fetchone()
    assert row is not None, "session did not reach the mirror"
    assert row["name"] == "Physics"
    assert isinstance(row["task_id"], int)
    assert row["duration_seconds"] == 5400


def test_goal_with_milestones_survives_the_trip(world):
    goal = world.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "add_goal",
                    "params": {"name": "Pass the exam"},
                }
            ]
        },
    ).json()["applied"][0]["result"]["goal"]
    for title in ("Chapter 1", "Chapter 2"):
        world.http.post(
            "/ops",
            json={
                "ops": [
                    {
                        "op_id": str(uuid.uuid4()),
                        "op": "add_milestone",
                        "params": {"goal_uid": goal["uid"], "title": title},
                    }
                ]
            },
        )
    world.engine.sync()

    local_goal_id = world.mirror.id_for_uid("todo_tasks", goal["uid"])
    cur = world.mirror.connection.cursor()
    titles = [
        r["title"]
        for r in cur.execute(
            "SELECT title FROM milestones WHERE goal_id = ? ORDER BY title",
            (local_goal_id,),
        )
    ]
    assert titles == ["Chapter 1", "Chapter 2"]


# ── pushing and the outbox ──────────────────────────────────────────────


def test_queued_work_reaches_the_server(world):
    subject_uid = server_add_subject(world, "Physics")
    world.engine.sync()

    state.enqueue(
        world.mirror.connection,
        "add_session",
        {
            "subject_uid": subject_uid,
            "start_time": "2026-06-02T09:00:00",
            "end_time": "2026-06-02T10:00:00",
            "uid": sync_policy.new_uid(),
        },
    )
    result = world.engine.sync()

    assert result.pushed == 1
    assert state.pending_count(world.mirror.connection) == 0
    assert counts(world.server_db)["sessions"] == 1


def test_work_done_offline_is_kept_and_sent_later(world):
    subject_uid = server_add_subject(world, "Physics")
    world.engine.sync()

    world.client.offline = True
    state.enqueue(
        world.mirror.connection,
        "add_goal",
        {"name": "Written while offline", "uid": sync_policy.new_uid()},
    )
    offline_result = world.engine.sync()

    assert offline_result.offline is True
    assert offline_result.pending == 1
    assert counts(world.server_db)["todo_tasks"] == 0

    world.client.offline = False
    online_result = world.engine.sync()

    assert online_result.pushed == 1
    assert counts(world.server_db)["todo_tasks"] == 1
    assert mirror_names(world, "todo_tasks") == ["Written while offline"]


def test_a_uid_minted_offline_is_the_uid_the_server_ends_up_with(world):
    """Otherwise the row comes back on the next pull as a second copy."""
    chosen = sync_policy.new_uid()
    world.client.offline = True
    state.enqueue(
        world.mirror.connection, "add_goal", {"name": "Offline goal", "uid": chosen}
    )
    world.engine.sync()
    world.client.offline = False
    world.engine.sync()

    assert world.server_db.id_for_uid("todo_tasks", chosen) is not None
    assert counts(world.server_db)["todo_tasks"] == 1
    world.engine.sync()
    assert counts(world.mirror)["todo_tasks"] == 1


def test_a_retry_after_a_lost_response_does_not_duplicate(world):
    """The queue is sent, the reply is lost, the client retries the same entry."""
    subject_uid = server_add_subject(world, "Physics")
    world.engine.sync()

    op_id = str(uuid.uuid4())
    params = {
        "subject_uid": subject_uid,
        "start_time": "2026-06-03T09:00:00",
        "end_time": "2026-06-03T10:00:00",
        "uid": sync_policy.new_uid(),
    }
    state.enqueue(world.mirror.connection, "add_session", params, op_id=op_id)

    # First attempt reaches the server, but the client never learns that.
    world.client.send_ops(state.pending(world.mirror.connection), device_id="macbook")
    assert counts(world.server_db)["sessions"] == 1

    # The entry is still queued, so the next pass sends it again.
    result = world.engine.sync()

    assert result.pushed == 1
    assert counts(world.server_db)["sessions"] == 1, "retry created a duplicate"
    assert counts(world.mirror)["sessions"] == 1


def test_a_refused_operation_blocks_the_queue_instead_of_being_dropped(world):
    state.enqueue(
        world.mirror.connection,
        "add_session",
        {
            "subject_uid": "no-such-subject",
            "start_time": "2026-06-01T10:00:00",
            "end_time": "2026-06-01T11:00:00",
        },
    )
    state.enqueue(world.mirror.connection, "add_goal", {"name": "Behind the bad one"})

    result = world.engine.sync()

    assert result.ok is False
    assert result.blocked_reason
    # Nothing lost, nothing reordered, and the good op did NOT jump the queue.
    assert state.pending_count(world.mirror.connection) == 2
    assert counts(world.server_db)["todo_tasks"] == 0
    head = state.pending(world.mirror.connection, limit=1)[0]
    assert head["attempts"] == 1
    assert head["last_error"]


def test_ops_are_sent_in_the_order_they_were_queued(world):
    for name in ("first", "second", "third"):
        state.enqueue(world.mirror.connection, "add_goal", {"name": name})
    world.engine.sync()

    sent = [o["params"]["name"] for o in world.client.sent_batches[-1]["ops"]]
    assert sent == ["first", "second", "third"]


# ── verification and self-repair ────────────────────────────────────────


def test_a_healthy_mirror_reports_no_drift(world):
    server_add_subject(world, "Physics")
    result = world.engine.sync()
    assert result.repaired is False
    assert world.engine.verify(result) is True


def test_a_damaged_mirror_repairs_itself(world):
    server_add_subject(world, "Physics")
    server_add_subject(world, "Maths")
    world.engine.sync()
    assert len(mirror_names(world)) == 2

    # Something corrupted the mirror behind our back.
    world.mirror.connection.execute("DELETE FROM tasks")
    world.mirror.connection.commit()
    assert mirror_names(world) == []

    result = world.engine.sync()

    assert result.repaired is True
    assert mirror_names(world) == ["Maths", "Physics"]


def test_repair_never_runs_while_the_outbox_has_unsent_work(world):
    """A full re-download wipes local rows, so it must not discard queued work."""
    server_add_subject(world, "Physics")
    world.engine.sync()

    world.mirror.connection.execute("DELETE FROM tasks")
    world.mirror.connection.commit()
    world.client.offline = True
    state.enqueue(world.mirror.connection, "add_goal", {"name": "Precious"})
    world.engine.sync()
    world.client.offline = False

    # Make the push fail so the queue survives into the verify step.
    world.mirror.connection.execute(
        "UPDATE sync_outbox SET op_name = 'no_such_op'"
    )
    world.mirror.connection.commit()
    result = world.engine.sync()

    assert result.repaired is False
    assert state.pending_count(world.mirror.connection) == 1


def test_full_resync_rebuilds_everything_including_shared_settings(world):
    subject_uid = server_add_subject(world, "Physics")
    world.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "add_session",
                    "params": {
                        "subject_uid": subject_uid,
                        "start_time": "2026-06-01T10:00:00",
                        "end_time": "2026-06-01T11:00:00",
                    },
                },
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "set_setting",
                    "params": {"key": "day_start_time", "value": "05:00"},
                },
            ]
        },
    )

    from jobtracker.sync.engine import SyncResult

    result = SyncResult()
    world.engine.full_resync(result)

    assert counts(world.mirror)["sessions"] == 1
    assert counts(world.mirror)["tasks"] == 1
    assert world.mirror.get_setting("day_start_time") == "05:00"


def test_device_local_settings_are_not_overwritten_by_a_resync(world):
    """The phone must never dictate this machine's theme."""
    world.mirror.set_setting("theme_fx", "Nebula")
    world.mirror.set_setting("graph_range", "months")

    from jobtracker.sync.engine import SyncResult

    world.engine.full_resync(SyncResult())

    assert world.mirror.get_setting("theme_fx") == "Nebula"
    assert world.mirror.get_setting("graph_range") == "months"


# ── convergence under a messy sequence ──────────────────────────────────


def test_mirror_converges_after_an_interleaved_mess(world):
    """Creates, edits, deletes and an outage, mixed together — the mirror must
    still end up exactly equal to the server."""
    physics = server_add_subject(world, "Physics")
    maths = server_add_subject(world, "Maths")
    world.engine.sync()

    # Server-side edits.
    world.http.post(
        "/ops",
        json={
            "ops": [
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "update_subject",
                    "params": {
                        "subject_uid": physics,
                        "name": "Physics II",
                        "color": "#EF4444",
                    },
                },
                {
                    "op_id": str(uuid.uuid4()),
                    "op": "delete_subject",
                    "params": {"subject_uid": maths},
                },
            ]
        },
    )

    # Local work made during an outage.
    world.client.offline = True
    for name in ("Offline A", "Offline B"):
        state.enqueue(
            world.mirror.connection,
            "add_goal",
            {"name": name, "uid": sync_policy.new_uid()},
        )
    world.engine.sync()
    world.client.offline = False

    result = world.engine.sync()
    assert result.ok

    assert counts(world.mirror) == counts(world.server_db)
    assert mirror_names(world) == ["Physics II"]
    assert sorted(mirror_names(world, "todo_tasks")) == ["Offline A", "Offline B"]
    assert state.pending_count(world.mirror.connection) == 0
    assert world.engine.verify(result) is True


# ── clock skew ──────────────────────────────────────────────────────────


def test_a_server_in_another_timezone_is_reported(world):
    """Naive local timestamps mean a server in the wrong timezone files work
    hours off, and every row still looks perfectly valid."""
    from datetime import datetime, timedelta
    from jobtracker.sync.engine import SyncResult

    real_integrity = world.client.integrity

    def skewed(deep=False):
        report = real_integrity(deep=deep)
        report["server_time"] = (datetime.now() - timedelta(hours=2)).isoformat()
        return report

    world.client.integrity = skewed
    result = SyncResult()
    world.engine.verify(result)

    assert any("clock" in m for m in result.messages), result.messages


def test_matching_clocks_produce_no_complaint(world):
    from jobtracker.sync.engine import SyncResult

    result = SyncResult()
    world.engine.verify(result)
    assert not any("clock" in m for m in result.messages)


def test_integrity_reports_the_servers_wall_clock(world):
    assert world.client.integrity().get("server_time")


def test_a_shared_setting_changed_elsewhere_reaches_the_mirror(world):
    """Settings are not rows, so they never appear in the change feed. Without
    this the day-start could be changed on the phone and the Mac would keep
    bucketing days by the old boundary forever."""
    world.engine.sync()
    assert world.mirror.get_setting("day_start_time", "03:00") in ("", "03:00")

    world.http.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "set_setting",
        "params": {"key": "day_start_time", "value": "05:00"}}]})
    world.engine.sync()

    assert world.mirror.get_setting("day_start_time") == "05:00"


def test_a_pull_does_not_touch_device_local_settings(world):
    world.mirror.set_setting("theme_fx", "Nebula")
    world.mirror.set_setting("graph_range", "months")
    world.engine.sync()
    assert world.mirror.get_setting("theme_fx") == "Nebula"
    assert world.mirror.get_setting("graph_range") == "months"
