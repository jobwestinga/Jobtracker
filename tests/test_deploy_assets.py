"""Release filters must ship web modules while excluding private inputs."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RSYNC = shutil.which("rsync")
pytestmark = pytest.mark.skipif(RSYNC is None, reason="rsync deployment tool unavailable")


def test_deployment_ships_every_web_asset_including_data_modules(tmp_path):
    subprocess.run([RSYNC, "-a", "--exclude-from=" + str(ROOT / "deploy/.rsync-exclude"),
                    str(ROOT / "web"), str(tmp_path)], check=True, capture_output=True)
    for source in (ROOT / "web").rglob("*"):
        if source.is_file() and source.name != ".DS_Store":
            target = tmp_path / "web" / source.relative_to(ROOT / "web")
            assert target.is_file(), f"Deployment excluded {source.relative_to(ROOT)}"
            assert target.read_bytes() == source.read_bytes()


def test_deployment_filter_excludes_private_data_and_configuration(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    for name in ("data/private.db", "deploy/server.env", "web/data/outbox.js", "server/app.py"):
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("synthetic fixture")
    target.mkdir()
    subprocess.run([RSYNC, "-a", "--exclude-from=" + str(ROOT / "deploy/.rsync-exclude"),
                    str(source) + "/", str(target)], check=True, capture_output=True)
    assert not (target / "data").exists()
    assert not (target / "deploy/server.env").exists()
    assert (target / "web/data/outbox.js").is_file()
    assert (target / "server/app.py").is_file()
