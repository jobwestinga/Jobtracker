"""Source files must stay visible to Git while private app data stays ignored."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="Git is unavailable")


def ignored_paths(root, paths):
    result = subprocess.run(
        [GIT, "-C", str(root), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(paths) + "\n", text=True, capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr
    return set(result.stdout.splitlines())


def test_git_filter_keeps_source_and_excludes_private_inputs(tmp_path):
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    subprocess.run([GIT, "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    private = {"data/jobtracker.db", "data/backups/history.json", "deploy/server.env",
               ".env", "secrets/tokens.json", "secrets/access.key"}
    source = {"web/data/actions.js", "web/data/outbox.js", "web/data/credentials.js",
              "jobtracker/core/persistence/sessions.py", "docs/architecture/features.json"}
    assert ignored_paths(tmp_path, sorted(private | source)) == private


def test_every_phone_runtime_asset_is_visible_to_git():
    assets = [path.relative_to(ROOT).as_posix() for path in (ROOT / "web").rglob("*")
              if path.is_file() and path.suffix in {".js", ".css", ".html", ".png", ".webmanifest"}]
    assert assets
    assert not ignored_paths(ROOT, assets), "A fresh checkout would omit phone runtime assets"
