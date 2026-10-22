"""Store selection and single-owner locking happen before any database import."""
import os
import signal
import subprocess
import sys

import pytest

from jobtracker.desktop import bootstrap
from jobtracker.desktop.ownership import StoreInUse, StoreLock


def test_isolated_mode_requires_an_explicit_path(tmp_path):
    with pytest.raises(ValueError, match="explicit --db"):
        bootstrap.resolve_store("isolated")
    selection = bootstrap.resolve_store("isolated", tmp_path / "pilot.db")
    assert selection.db_path == bootstrap.canonical(tmp_path / "pilot.db")
    assert selection.data_dir == bootstrap.canonical(tmp_path)


def test_isolated_mode_never_opens_a_real_store():
    for real in (bootstrap.production_db_path(), bootstrap.development_db_path()):
        with pytest.raises(ValueError, match="must not open"):
            bootstrap.resolve_store("isolated", real)


def test_named_modes_resolve_their_own_paths_and_reject_overrides(tmp_path):
    assert bootstrap.resolve_store("production").db_path == bootstrap.canonical(bootstrap.production_db_path())
    assert bootstrap.resolve_store("development").db_path == bootstrap.canonical(bootstrap.development_db_path())
    for mode in ("production", "development"):
        with pytest.raises(ValueError, match="do not pass --db"):
            bootstrap.resolve_store(mode, tmp_path / "x.db")
    with pytest.raises(ValueError, match="Unknown engine mode"):
        bootstrap.resolve_store("prototype")


def test_frozen_helper_cannot_select_the_checkout_store(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ValueError, match="source checkout"):
        bootstrap.resolve_store("development")


def test_selection_refuses_a_database_module_bound_elsewhere(tmp_path):
    # The test suite already imported config pointing at its singleton store.
    with pytest.raises(RuntimeError, match="imported before"):
        bootstrap.apply_environment(bootstrap.resolve_store("isolated", tmp_path / "other.db"))


def test_store_lock_is_exclusive_and_released(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_LOCK_DIR", str(tmp_path / "locks"))
    store = tmp_path / "store.db"
    first = StoreLock(store, role="qt").acquire()
    with pytest.raises(StoreInUse) as error:
        StoreLock(store, role="native-engine").acquire()
    assert error.value.owner.get("role") == "qt"
    first.release()
    StoreLock(store, role="native-engine").acquire().release()


def test_store_lock_is_keyed_by_canonical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_LOCK_DIR", str(tmp_path / "locks"))
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    with StoreLock(tmp_path / "real" / "store.db", role="qt"):
        with pytest.raises(StoreInUse):
            StoreLock(tmp_path / "alias" / "store.db", role="native-engine").acquire()


def test_a_crashed_owner_leaves_no_live_lock(tmp_path):
    env = dict(os.environ, JOBTRACKER_LOCK_DIR=str(tmp_path / "locks"))
    store = tmp_path / "store.db"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; from jobtracker.desktop.ownership import StoreLock; "
         f"StoreLock({str(store)!r}, role='qt').acquire(); print('held', flush=True); time.sleep(60)"],
        env=env, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        os.environ["JOBTRACKER_LOCK_DIR"] = env["JOBTRACKER_LOCK_DIR"]
        with pytest.raises(StoreInUse):
            StoreLock(store, role="native-engine").acquire()
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=10)
        StoreLock(store, role="native-engine").acquire().release()
        assert StoreLock(store, role="qt").path.exists()  # the file alone is not a lock
    finally:
        os.environ.pop("JOBTRACKER_LOCK_DIR", None)
        if holder.poll() is None:
            holder.kill()
        holder.stdout.close()
