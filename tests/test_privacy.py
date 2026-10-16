"""Repository privacy barriers use synthetic content and isolated Git indexes."""
import json
from pathlib import Path
import subprocess
import struct

import pytest

from scripts import check_privacy


@pytest.mark.parametrize("name", ["data/history.json", "exports/readings.json", "weights.csv", "scale.xlsx",
                                  "history.sqlite3-wal", "deploy/server.env", "sync_token", ".env.local",
                                  ".local/notes.md", "backups/history.zip"])
def test_private_files_are_rejected_even_if_empty(name):
    assert check_privacy.inspect(name, b"")


@pytest.mark.parametrize("name", ["web/data/outbox.js", "deploy/server.env.example", "tests/mobile/fixtures/weight_math.json"])
def test_public_sources_and_templates_remain_allowed(name):
    assert not check_privacy.inspect(name, b"synthetic public source")


def test_private_values_are_reported_without_printing_them():
    secret = "ghp_" + "a" * 36
    address = ".".join(["100", "90", "12", "34"])
    home = "/Users/" + "private-person" + "/Documents"
    errors = check_privacy.inspect("example.md", f"{secret}\n{address}\n{home}".encode())
    assert len(errors) == 3
    assert all(value not in "\n".join(errors) for value in (secret, address, home))


def test_measurement_json_is_private_and_test_fixtures_are_explicitly_scoped():
    data = json.dumps({"measurements": [{"measured_at": "2026-01-01", "weight_kg": 80}]}).encode()
    assert check_privacy.inspect("web/history.json", data)
    assert not check_privacy.inspect("tests/fixtures/synthetic.json", data)


def test_embedded_image_metadata_is_private_but_colour_profiles_are_allowed():
    signature = b"\x89PNG\r\n\x1a\n"
    def chunk(kind):
        return struct.pack(">I", 0) + kind + b"\0" * 4
    assert check_privacy.inspect("web/icon.png", signature + chunk(b"iTXt"))
    assert check_privacy.inspect("web/icon.png", signature + chunk(b"eXIf"))
    assert not check_privacy.inspect("web/icon.png", signature + chunk(b"iCCP") + chunk(b"IEND"))


def test_force_added_file_and_staged_secret_cannot_hide_behind_working_tree(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.csv\n")
    (tmp_path / "weights.csv").write_text("synthetic private export")
    subprocess.run(["git", "add", "-f", "weights.csv"], cwd=tmp_path, check=True)
    assert any("weights.csv" in error for error in check_privacy.check(tmp_path, staged=True))
    subprocess.run(["git", "rm", "--cached", "-f", "weights.csv"], cwd=tmp_path, check=True, capture_output=True)
    secret = "sk-" + "a" * 40
    (tmp_path / "readme.md").write_text(secret)
    subprocess.run(["git", "add", "readme.md"], cwd=tmp_path, check=True)
    (tmp_path / "readme.md").write_text("now safe on disk")
    errors = check_privacy.check(tmp_path, staged=True)
    assert any("API secret" in error for error in errors)
    assert secret not in "\n".join(errors)
