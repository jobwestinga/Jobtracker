"""Real-process engine lifecycle: hello, single owner, crash, EOF and restart."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest

from jobtracker.desktop.ownership import StoreLock
from tests.support.desktop import ROOT, EngineProcess

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process lifecycle")


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "pilot" / "store.db", tmp_path / "locks"


def _open_sessions(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT uid, end_time FROM sessions WHERE end_time IS NULL").fetchall()


def test_hello_reports_store_commands_and_no_qt(paths):
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    try:
        hello = engine.first
        assert hello["kind"] == "hello" and hello["protocol_version"] == 1
        assert hello["mode"] == "isolated" and hello["db_path"] == os.path.realpath(db_path)
        assert hello["qt_loaded"] is False
        assert {c["name"] for c in hello["commands"]} >= {"subjects.list", "session.shift", "timer.start"}
        info = engine.send("engine.info")
        assert info["result"]["engine_instance"] == hello["engine_instance"]
    finally:
        assert engine.close() == 0


def test_second_engine_on_the_same_store_refuses_before_recovery(paths):
    db_path, locks = paths
    first = EngineProcess(db_path, locks)
    try:
        second = EngineProcess(db_path, locks)
        assert second.first == {"protocol_version": 1, "kind": "fatal", "reason": "store_in_use",
                                "message": "JobTracker is already open with this data."}
        assert second.process.wait(timeout=10) == 3
        second.close()
        assert first.send("engine.info")["outcome"] == "ok"
    finally:
        first.close()


def test_qt_lock_holder_blocks_the_engine(paths, monkeypatch):
    db_path, locks = paths
    db_path.parent.mkdir(parents=True)
    monkeypatch.setenv("JOBTRACKER_LOCK_DIR", str(locks))
    with StoreLock(db_path, role="qt"):
        engine = EngineProcess(db_path, locks)
        assert engine.first["reason"] == "store_in_use"
        engine.close()


def test_owner_loss_keeps_the_running_session_and_restart_resumes_it(paths):
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    subject = engine.send("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["result"]["subject"]
    started = engine.send("timer.start", {"subject_uid": subject["uid"], "at": (datetime.now() - timedelta(minutes=2)).isoformat()})
    session_uid = started["result"]["active"]["uid"]
    assert engine.close() == 0  # parent EOF: no implicit stop
    assert _open_sessions(db_path) == [(session_uid, None)]

    restarted = EngineProcess(db_path, locks)
    try:
        timer = restarted.send("timer.get")["result"]
        assert timer["active"]["uid"] == session_uid
        assert timer["recovery"] is None  # brief gap: resumed without a prompt
    finally:
        restarted.close()
    assert _open_sessions(db_path) == [(session_uid, None)]


def test_crash_while_tracking_preserves_the_session_for_the_next_owner(paths):
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    subject = engine.send("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})["result"]["subject"]
    started = engine.send("timer.start", {"subject_uid": subject["uid"], "at": (datetime.now() - timedelta(hours=2)).isoformat()})
    session_uid = started["result"]["active"]["uid"]
    engine.process.send_signal(signal.SIGKILL)
    engine.kill()
    assert _open_sessions(db_path) == [(session_uid, None)]

    restarted = EngineProcess(db_path, locks)
    try:
        timer = restarted.send("timer.get")["result"]
        assert timer["active"]["uid"] == session_uid
        assert timer["recovery"] is not None  # the two-hour gap needs a decision
    finally:
        restarted.close()


def test_sigterm_settles_like_owner_loss(paths):
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    engine.send("subject.add", {"name": "Physics", "color": "#3B82F6", "notes": ""})
    engine.process.send_signal(signal.SIGTERM)
    assert engine.process.wait(timeout=20) == 0
    engine.close()


def test_isolated_mode_cannot_target_the_installed_store(tmp_path):
    from jobtracker.desktop.bootstrap import production_db_path

    env = dict(os.environ, JOBTRACKER_LOCK_DIR=str(tmp_path / "locks"))
    env.pop("JOBTRACKER_DB_PATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "jobtracker.desktop", "--mode", "isolated", "--db", str(production_db_path())],
        cwd=ROOT, env=env, capture_output=True, timeout=30,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "invalid_store"


def test_stray_prints_never_corrupt_the_protocol_stream(paths):
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    try:
        engine.process.stdin.write(b'not json\n')
        engine.process.stdin.flush()
        bad = engine.read()
        assert bad["kind"] == "response" and bad["outcome"] == "invalid"
        assert engine.send("engine.info")["outcome"] == "ok"
    finally:
        engine.close()


def test_engine_startup_is_quick_enough_for_launch(paths):
    db_path, locks = paths
    began = time.monotonic()
    engine = EngineProcess(db_path, locks)
    elapsed = time.monotonic() - began
    engine.close()
    assert elapsed < 10


def test_qt_entry_point_refuses_a_store_owned_by_the_engine(paths):
    pytest.importorskip("PySide6")
    db_path, locks = paths
    engine = EngineProcess(db_path, locks)
    try:
        env = dict(os.environ, JOBTRACKER_LOCK_DIR=str(locks), JOBTRACKER_DB_PATH=str(db_path), QT_QPA_PLATFORM="offscreen")
        script = (
            "from PySide6.QtWidgets import QMessageBox\n"
            "QMessageBox.information = staticmethod(lambda *a, **k: print('notified', flush=True))\n"
            "import main\n"
            "main.MainWindow = None  # must never be constructed\n"
            "main.main()\n"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 1, result.stderr
        assert "notified" in result.stdout
        assert engine.send("engine.info")["outcome"] == "ok"
    finally:
        engine.close()
