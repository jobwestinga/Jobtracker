"""Run dependency-free JavaScript regressions as part of the normal suite."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_mobile_outbox_and_offline_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; mobile JavaScript tests need Node 18+")
    root = Path(__file__).resolve().parents[1]
    tests = sorted((root / "tests" / "mobile").glob("*.test.mjs"))
    result = subprocess.run([node, "--test", *map(str, tests)], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
