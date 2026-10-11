"""The plotting bridge never exposes a partially written personal history."""
import json
import stat

import pytest

from jobtracker.sync.client import SyncClient
from server import import_weights


def _export_args(tmp_path, target):
    token = tmp_path / "token"
    token.write_text("test-token")
    return ["--source", str(tmp_path), "--server", "https://example.invalid",
            "--token-file", str(token), "--export-json", str(target)]


def test_export_replaces_history_privately_without_reading_source(tmp_path, monkeypatch):
    readings = [{"measured_at": "2026-10-10T07:00:00", "weight_kg": 80, "source": "manual"}]
    requests = []

    def request(self, method, path):
        requests.append((method, path))
        return {"measurements": readings}

    monkeypatch.setattr(SyncClient, "_request", request)
    target = tmp_path / "JobTrackerWeights.json"
    target.write_text("previous history")
    import_weights.main(_export_args(tmp_path, target))
    assert json.loads(target.read_text()) == readings
    assert requests == [("GET", "/api/weight")]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".jobtracker-weights-*"))


def test_failed_export_preserves_previous_complete_history(tmp_path, monkeypatch):
    monkeypatch.setattr(SyncClient, "_request", lambda *args: {"measurements": []})
    target = tmp_path / "JobTrackerWeights.json"
    previous = '[{"weight_kg":80}]'
    target.write_text(previous)

    def fail_replace(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(import_weights.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        import_weights.main(_export_args(tmp_path, target))
    assert target.read_text() == previous
    assert not list(tmp_path.glob(".jobtracker-weights-*"))
