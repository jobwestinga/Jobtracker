"""A synced service, mirror and real in-process server on temporary databases."""
import pytest
TestClient = pytest.importorskip("fastapi.testclient", reason="server extras not installed").TestClient
from jobtracker.core import sync_policy
from jobtracker.core.database import Database
from jobtracker.sync.service import SyncedTrackerService
from jobtracker.sync.engine import SyncEngine
from server import app as app_module
from server import auth
from tests.support.sync import InProcessClient


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

