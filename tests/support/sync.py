"""Shared sync test adapters and isolated fixtures."""

import uuid

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="server extras not installed"
)

from jobtracker.core.database import Database  # noqa: E402
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

