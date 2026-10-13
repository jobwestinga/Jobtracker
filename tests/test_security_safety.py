"""Credentials, redirect leakage, malformed inputs and idempotency boundaries."""

import io
import json
import ssl
import urllib.error
import urllib.request

import pytest

pytest.importorskip("fastapi.testclient", reason="server extras not installed")

from jobtracker.core import sync_policy
from jobtracker.sync import client as client_module
from jobtracker.sync.client import SyncClient, SyncError
from server import app as app_module, auth
from tests.support.api import api_routes
from tests.support.sync import world


@pytest.mark.parametrize("malformed", [None, 7, "broken", {}, {"name": "bad", "sha256": "bad"}, {"sha256": "0" * 64}])
def test_corrupt_token_entries_fail_closed(tmp_path, monkeypatch, malformed):
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(path))
    path.write_text(json.dumps([malformed]))
    assert auth.device_for_token("guess") is None


def test_malformed_entries_do_not_disable_valid_device_tokens(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(path))
    token = "real secret"
    path.write_text(json.dumps([None, 42, {"name": "mac", "sha256": auth.hash_token(token)}]))
    assert auth.device_for_token(token) == "mac"


def test_auth_file_is_private_atomic_and_contains_only_hashes(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(path))
    first = auth.issue_token("mac")
    assert path.stat().st_mode & 0o777 == 0o600
    old_inode = path.stat().st_ino
    second = auth.issue_token("phone")
    assert path.stat().st_ino != old_inode
    assert path.stat().st_mode & 0o777 == 0o600
    assert first not in path.read_text() and second not in path.read_text()
    assert auth.device_for_token(first) == "mac"
    assert auth.device_for_token(second) == "phone"
    assert not list(tmp_path.glob(".tokens-*"))


@pytest.mark.parametrize("name", ["", "  ", None, 12])
def test_empty_or_invalid_device_names_are_not_issued(tmp_path, monkeypatch, name):
    monkeypatch.setenv("JOBTRACKER_TOKENS_PATH", str(tmp_path / "tokens.json"))
    with pytest.raises(ValueError, match="name"):
        auth.issue_token(name)


@pytest.mark.parametrize("url", [
    "http://remote.example", "file:///etc/passwd", "ftp://remote.example", "https://user:password@example.org",
    "https://example.org?token=secret", "https://example.org#fragment", "https://example.org:bad", "https://",
])
def test_invalid_or_cleartext_remote_address_never_sends_credentials(monkeypatch, url):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: pytest.fail("must reject before transport"))
    with pytest.raises(SyncError) as error:
        SyncClient(url, "private access token").snapshot()
    assert error.value.status == 400
    assert "private access token" not in str(error.value)


def test_redirect_does_not_forward_bearer_token():
    request = urllib.request.Request("https://personal.example/api/snapshot")
    request.add_header("Authorization", "Bearer private access token")
    with pytest.raises(urllib.error.HTTPError) as error:
        client_module._NoRedirect().redirect_request(
            request, io.BytesIO(b""), 302, "Found", {}, "https://unrelated.example/collect"
        )
    assert error.value.code == 302


def test_tls_verification_remains_enabled():
    context = client_module.build_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.mark.parametrize("body", [b"[]", b'"string"', b"null", b"{invalid", b"\xff"])
def test_malformed_server_responses_are_reported_as_protocol_errors(monkeypatch, body):
    class Opener:
        def open(self, request, timeout):
            return _Response(body)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(SyncError) as error:
        SyncClient("http://localhost", "secret").snapshot()
    assert error.value.status == 502
    assert not error.value.offline


def test_nonobject_http_error_body_is_not_an_unhandled_exception(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 500, "Error", {}, io.BytesIO(b'["oops"]'))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(SyncError) as error:
        SyncClient("http://localhost", "secret").snapshot()
    assert error.value.status == 500


def test_all_data_routes_require_authentication(world):
    world.http.headers.pop("Authorization")
    checked = []
    for _index, route in api_routes(app_module.app):
        path = route.path
        if path.startswith(("/api/", "/sync/")) or path == "/ops/known":
            # Dependency auth runs before route parameter parsing for these
            # valid representative paths, including date/path parameters.
            concrete = path.replace("{day}", "2026-06-01")
            response = world.http.get(concrete)
            assert response.status_code == 401, (concrete, response.text)
            checked.append(path)
    assert "/api/context" in checked and "/sync/pull" in checked, checked
    response = world.http.post("/ops", json={"ops": [{"op_id": sync_policy.new_uid(), "op": "add_goal", "params": {"name": "No permission"}}]})
    assert response.status_code == 401
    assert world.server_db.get_all_todo_tasks() == []


def test_malformed_operation_blocks_following_writes_and_reports_saved_prefix(world):
    response = world.http.post("/ops", json={"ops": [
        {"op_id": sync_policy.new_uid(), "op": "add_goal", "params": {"name": "Saved"}},
        {"op_id": sync_policy.new_uid(), "op": "add_subject", "params": {}},
        {"op_id": sync_policy.new_uid(), "op": "add_goal", "params": {"name": "Must wait"}},
    ]})
    assert response.status_code == 400
    assert response.json()["failed_index"] == 1
    assert len(response.json()["applied"]) == 1
    assert [goal.name for goal in world.server_db.get_all_todo_tasks()] == ["Saved"]
    assert world.server_db.get_all_subjects() == []


def test_op_id_cannot_be_reused_for_different_parameters(world):
    identifier = sync_policy.new_uid()
    request = {"op_id": identifier, "op": "add_goal", "params": {"name": "First"}}
    assert world.http.post("/ops", json={"ops": [request]}).status_code == 200
    request["params"]["name"] = "Different"
    response = world.http.post("/ops", json={"ops": [request]})
    assert response.status_code == 409
    assert [goal.name for goal in world.server_db.get_all_todo_tasks()] == ["First"]


def test_another_token_cannot_replay_by_spoofing_original_device_id(world):
    identifier = sync_policy.new_uid()
    request = {"op_id": identifier, "op": "add_goal", "device_id": "install-uuid", "params": {"name": "First"}}
    assert world.http.post("/ops", json={"ops": [request]}).status_code == 200
    phone_token = auth.issue_token("phone")
    world.http.headers["Authorization"] = f"Bearer {phone_token}"
    response = world.http.post("/ops", json={"ops": [request]})
    assert response.status_code == 409


def test_feed_request_hash_migration_is_idempotent(world):
    from server import feed
    connection = world.server_db.connection
    feed.install(connection)
    feed.install(connection)
    columns = [row["name"] for row in connection.execute("PRAGMA table_info(applied_ops)")]
    assert columns.count("request_hash") == 1


@pytest.mark.parametrize("operation, parameters, table", [
    ("add_subject", {"name": "Subject"}, "tasks"),
    ("add_goal", {"name": "Goal"}, "todo_tasks"),
    ("add_template", {"title": "Template", "recurrence": "daily"}, "goal_templates"),
])
def test_duplicate_create_uid_is_rejected_and_entire_mutation_rolls_back(world, operation, parameters, table):
    identity = sync_policy.new_uid()
    first = {"op_id": sync_policy.new_uid(), "op": operation, "params": {**parameters, "uid": identity}}
    assert world.http.post("/ops", json={"ops": [first]}).status_code == 200
    before_rows = [dict(row) for row in world.server_db.connection.execute(f"SELECT * FROM {table}")]
    before_feed = world.server_db.connection.execute("SELECT COUNT(*) FROM change_feed").fetchone()[0]
    second_params = {**parameters, "uid": identity}
    second_params["title" if "title" in parameters else "name"] = "Different title"
    second_id = sync_policy.new_uid()
    response = world.http.post("/ops", json={"ops": [{"op_id": second_id, "op": operation, "params": second_params}]})
    assert response.status_code == 409
    assert [dict(row) for row in world.server_db.connection.execute(f"SELECT * FROM {table}")] == before_rows
    assert world.server_db.connection.execute("SELECT COUNT(*) FROM change_feed").fetchone()[0] == before_feed
    assert world.server_db.connection.execute("SELECT 1 FROM applied_ops WHERE op_id = ?", (second_id,)).fetchone() is None


def test_duplicate_session_uid_cannot_create_an_extra_session(world):
    subject = world.server_svc.add_subject("Subject", "#123456", "")
    identity = sync_policy.new_uid()
    params = {"subject_uid": subject.uid, "uid": identity, "start_time": "2026-06-01T10:00:00", "end_time": "2026-06-01T11:00:00"}
    first = {"op_id": sync_policy.new_uid(), "op": "add_session", "params": params}
    assert world.http.post("/ops", json={"ops": [first]}).status_code == 200
    response = world.http.post("/ops", json={"ops": [{**first, "op_id": sync_policy.new_uid()}]})
    assert response.status_code == 409
    assert world.server_db.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_retry_with_identical_payload_replays_original_result(world):
    identifier = sync_policy.new_uid()
    request = {"op_id": identifier, "op": "add_goal", "device_id": "install-uuid", "params": {"name": "Same request"}}
    first = world.http.post("/ops", json={"ops": [request]})
    second = world.http.post("/ops", json={"ops": [request]})
    assert first.status_code == second.status_code == 200
    original = first.json()["applied"][0]["result"]
    replayed = second.json()["applied"][0]["result"]
    assert original["replayed"] is False
    assert replayed["replayed"] is True
    assert original["goal"] == replayed["goal"]
    assert len(world.server_db.get_all_todo_tasks()) == 1


def test_private_api_response_headers_prevent_network_cache_storage(world):
    response = world.http.get("/api/snapshot")
    assert response.headers["Cache-Control"] == "no-store"
    assert "Authorization" in response.headers["Vary"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_nonfinite_operation_params_are_refused_before_mutation(world):
    request = json.dumps({"ops": [{"op_id": sync_policy.new_uid(), "op": "add_goal", "params": {"name": "Invalid number", "value": float("nan")}}]})
    response = world.http.post("/ops", content=request, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert world.server_db.get_all_todo_tasks() == []


@pytest.mark.parametrize("params", [
    {"name": {"bad": "object"}}, {"name": []},
    {"name": "Invalid color", "color": {}},
    {"name": "Invalid identity", "uid": {"bad": "object"}},
    {"name": "Invalid identity", "uid": []},
    {"name": "Invalid identity", "uid": 0},
    {"name": "Invalid identity", "uid": ""},
    {"name": "Invalid identity", "uid": "not-a-uuid"},
])
def test_malformed_scalar_fields_are_clean_client_errors_with_atomic_rollback(world, params):
    op_id = sync_policy.new_uid()
    before_feed = world.server_db.connection.execute("SELECT COUNT(*) FROM change_feed").fetchone()[0]
    response = world.http.post("/ops", json={"ops": [{"op_id": op_id, "op": "add_subject", "params": params}]})
    assert response.status_code == 400, response.text
    assert world.server_db.get_all_subjects() == []
    assert world.server_db.connection.execute("SELECT COUNT(*) FROM change_feed").fetchone()[0] == before_feed
    assert world.server_db.connection.execute("SELECT 1 FROM applied_ops WHERE op_id = ?", (op_id,)).fetchone() is None
