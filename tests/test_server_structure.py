"""Composition boundaries that keep extracted HTTP/operation adapters safe."""

from types import SimpleNamespace
import uuid

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.routing import APIRoute  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from server import app as composition  # noqa: E402
from server import auth, dependencies, ops, runtime  # noqa: E402
from server.operations import register_operations  # noqa: E402

TestClient = fastapi_testclient.TestClient

ROUTE_OWNERS = {
    "/ops": ("POST", "ops", "apply_ops"),
    "/ops/known": ("GET", "ops", "known_ops"),
    "/sync/pull": ("GET", "sync", "sync_pull"),
    "/sync/integrity": ("GET", "sync", "sync_integrity"),
    "/api/snapshot": ("GET", "sync", "api_snapshot"),
    "/api/active": ("GET", "context", "api_active"),
    "/api/context": ("GET", "context", "api_context"),
    "/api/graphs/breakdown": ("GET", "graphs", "api_breakdown"),
    "/api/graphs/agenda": ("GET", "graphs", "api_agenda"),
    "/api/sessions/day/{day}": ("GET", "sessions", "api_sessions_for_day"),
    "/api/weight": ("GET", "weight", "api_weight"),
}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    token = auth.issue_token("structure-test")
    installed = composition.build_state(str(tmp_path / "server.db"))
    for key, value in installed.items():
        monkeypatch.setitem(composition._state, key, value)
    client = TestClient(composition.app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield SimpleNamespace(client=client, **installed)
    finally:
        client.close()
        installed["db"].connection.close()


def test_domain_routes_keep_names_and_precede_root_static_mount():
    routes = composition.app.routes
    web_index = next(index for index, route in enumerate(routes)
                     if isinstance(route, Mount) and route.name == "web")
    owned = {route.path: (index, route) for index, route in enumerate(routes)
             if isinstance(route, APIRoute) and route.path in ROUTE_OWNERS}
    assert set(owned) == set(ROUTE_OWNERS)
    for path, (method, module, name) in ROUTE_OWNERS.items():
        index, route = owned[path]
        assert index < web_index, path
        assert route.methods == {method}, path
        assert route.name == name, path
        assert route.endpoint.__module__ == f"server.routes.{module}", path
        assert getattr(composition, name) is route.endpoint, path


def test_composition_aliases_share_runtime_and_authentication_identity():
    assert composition._state is runtime._state
    assert composition._lock is runtime._lock
    assert composition.svc is runtime.svc
    assert composition.require_device is dependencies.require_device
    for route in composition.app.routes:
        if isinstance(route, APIRoute) and route.path in ROUTE_OWNERS:
            calls = [dependency.call for dependency in route.dependant.dependencies]
            assert calls == [composition.require_device], route.path


def test_auth_override_applies_to_every_domain(server, monkeypatch):
    server.client.headers.pop("Authorization")
    assert server.client.get("/api/context").status_code == 401
    monkeypatch.setitem(composition.app.dependency_overrides,
                        composition.require_device, lambda: "overridden-device")
    for path in ("/api/context", "/api/sessions/day/2026-10-11",
                 "/api/graphs/agenda?days=1", "/api/weight",
                 "/sync/integrity", "/ops/known"):
        assert server.client.get(path).status_code == 200, path
    response = server.client.post("/ops", json={"ops": [{
        "op_id": str(uuid.uuid4()), "op": "add_subject",
        "params": {"name": "Override auth"},
    }]})
    assert response.status_code == 200


def test_unready_runtime_retains_http_503_after_authentication(server, monkeypatch):
    monkeypatch.setitem(composition._state, "svc", None)
    assert server.client.get("/health").json() == {
        "status": "starting", "version": composition.app.version,
    }
    for path in ("/api/context", "/api/weight", "/sync/pull"):
        response = server.client.get(path)
        assert response.status_code == 503, path
        assert response.json() == {"detail": "server not ready"}


def test_every_data_route_holds_shared_lock_before_sql(server):
    observed = []
    server.db.connection.set_trace_callback(lambda _sql: observed.append(runtime._lock.locked()))
    try:
        for path in ("/api/context", "/api/active", "/api/snapshot",
                     "/api/sessions/day/2026-10-11", "/api/graphs/breakdown?days=1",
                     "/api/graphs/agenda?days=1", "/api/weight",
                     "/sync/pull", "/sync/integrity?deep=true"):
            observed.clear()
            response = server.client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert observed and all(observed), path
        observed.clear()
        response = server.client.post("/ops", json={"ops": [{
            "op_id": str(uuid.uuid4()), "op": "add_subject",
            "params": {"name": "Serialized write"},
        }]})
        assert response.status_code == 200
        assert observed and all(observed)
        assert not runtime._lock.locked()
    finally:
        server.db.connection.set_trace_callback(None)


def test_explicit_domain_registration_matches_runtime_without_side_effects():
    before = dict(ops._HANDLERS)
    registered = {}

    def capture(name):
        def register(handler):
            assert name not in registered, f"duplicate operation: {name}"
            registered[name] = handler
            return handler
        return register

    register_operations(capture)
    assert registered == before
    assert ops._HANDLERS == before
    assert ops.known_ops() == sorted(registered)
    for name, handler in registered.items():
        assert handler.__name__ == name
        assert handler.__module__.startswith("server.operations.")
