"""Shared api test adapters and isolated fixtures."""

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

