"""Verified macOS replacement with idle guards, backups and code rollback.

No application imports: guard queries open existing SQLite files read-only and
never run migrations. A copy of this self-contained script runs from a private
stable folder when a LaunchAgent must wait for tracking/outbox work to finish.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

APP_ID = "com.jobtracker.app"
UPDATE_LABEL = "com.jobtracker.install"
STABLE_IDLE_SECONDS = 25
PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION = Path("/Applications/JobTracker.app")


class InstallError(RuntimeError):
    pass


class BusyInstall(InstallError):
    pass


def default_databases(project: Path) -> list[Path]:
    return [Path.home() / "Library/Application Support/JobTracker/jobtracker.db",
            project / "data/jobtracker.db"]


def read_work(databases, *, allow_missing=False) -> dict:
    """Read all devices' timers and every unacknowledged local operation."""
    active, pending, checked = 0, 0, []
    paths = dict.fromkeys(str(Path(path).expanduser().resolve()) for path in databases)
    if not paths:
        raise InstallError("At least one required database path is needed")
    for item in paths:
        path = Path(item)
        if not path.is_file():
            if allow_missing and not path.exists():
                continue
            raise InstallError(f"Required database is missing or unreadable: {path}")
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "sessions" not in tables:
                    raise InstallError(f"Required database has no sessions table: {path}")
                active += int(connection.execute("SELECT COUNT(*) FROM sessions WHERE end_time IS NULL").fetchone()[0])
                if "sync_outbox" in tables:
                    pending += int(connection.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
                checked.append(str(path))
        except sqlite3.Error as error:
            raise InstallError(f"Could not inspect required database: {path}: {error}") from error
    return {"active_sessions": active, "pending_operations": pending, "databases": checked,
            "idle": active == 0 and pending == 0}


def app_running(application: Path, *, run=subprocess.run) -> bool:
    executable = application / "Contents/MacOS/JobTracker"
    result = run(["pgrep", "-f", "^" + re.escape(str(executable)) + "($| )"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode not in (0, 1):
        raise InstallError("Could not inspect the installed app process")
    return result.returncode == 0


def inspect_install_state(application=APPLICATION, databases=None, *, project=PROJECT_ROOT,
                          allow_missing=False, run=subprocess.run) -> dict:
    application = Path(application).expanduser().resolve()
    work = read_work(default_databases(Path(project)) if databases is None else databases,
                     allow_missing=allow_missing)
    return {**work, "application": str(application), "running": app_running(application, run=run)}


def validate_bundle(application: Path) -> None:
    executable = application / "Contents/MacOS/JobTracker"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise InstallError(f"Staged app executable is missing or not executable: {application}")
    try:
        with (application / "Contents/Info.plist").open("rb") as source:
            info = plistlib.load(source)
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise InstallError("Staged app has an unreadable Info.plist") from error
    if info.get("CFBundleIdentifier") != APP_ID:
        raise InstallError("Staged bundle is not JobTracker")


def verify_bundle(application: Path, *, run=subprocess.run) -> None:
    validate_bundle(application)
    run(["codesign", "--verify", "--deep", "--strict", str(application)],
        check=True, capture_output=True)


def sign_bundle(application: Path, *, run=subprocess.run) -> None:
    validate_bundle(application)
    run(["xattr", "-cr", str(application)], check=True, capture_output=True)
    run(["codesign", "--force", "--deep", "-s", "-", str(application)],
        check=True, capture_output=True)
    verify_bundle(application, run=run)


def bundle_fingerprint(application: Path) -> str | None:
    if not application.exists():
        return None
    digest = hashlib.sha256()
    for relative in ("Contents/MacOS/JobTracker", "Contents/Info.plist", "Contents/_CodeSignature/CodeResources"):
        path = application / relative
        digest.update(relative.encode())
        if path.is_file():
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


@contextmanager
def _install_lock(update_root: Path):
    update_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (update_root / "install.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BusyInstall("Another installer is already checking the app") from error
        yield


def _normal_quit(application: Path, *, run=subprocess.run, sleep=time.sleep) -> None:
    if not app_running(application, run=run):
        return
    script = ("ObjC.import('AppKit'); var apps = $.NSRunningApplication."
              "runningApplicationsWithBundleIdentifier(" + json.dumps(APP_ID) + ");"
              "for (var i=0; i<apps.count; i++) { var current=apps.objectAtIndex(i);"
              "if (ObjC.unwrap(current.bundleURL.path)===" + json.dumps(str(application)) + ")"
              " { current.terminate; } }")
    run(["osascript", "-l", "JavaScript", "-e", script], check=True, capture_output=True)
    for _ in range(40):
        if not app_running(application, run=run):
            return
        sleep(0.25)
    raise BusyInstall("Normal quit was refused; the installed app is intact")


def _backup(databases, application: Path, update_root: Path, *, allow_missing=False) -> Path:
    folder = update_root / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, mode=0o700)
    for index, item in enumerate(dict.fromkeys(str(Path(path).expanduser().resolve()) for path in databases)):
        path = Path(item)
        if not path.exists() and allow_missing:
            continue
        destination = folder / f"database-{index + 1}.db"
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as source:
                with sqlite3.connect(destination) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise InstallError("Database backup integrity check failed")
        except sqlite3.Error as error:
            raise InstallError(f"Could not back up required database: {path}") from error
        destination.chmod(0o600)
    if application.exists():
        shutil.copytree(application, folder / "PreviousJobTracker.app", symlinks=True)
    _write_json(folder / "sources.json", {"databases": [str(Path(path)) for path in databases],
                                           "application": str(application)})
    return folder


def _install_ready(replacement: Path, application: Path, databases, update_root: Path,
                   *, allow_missing=False, relaunch=True, run=subprocess.run, sleep=time.sleep) -> dict:
    if not read_work(databases, allow_missing=allow_missing)["idle"]:
        raise BusyInstall("Tracking or queued changes appeared before installation")
    was_running = app_running(application, run=run)
    stage = application.parent / (".JobTracker-staged-" + uuid.uuid4().hex + ".app")
    previous = application.parent / (".JobTracker-previous-" + uuid.uuid4().hex + ".app")
    moved_previous = installed_new = False
    try:
        shutil.copytree(replacement, stage, symlinks=True)
        # Sign after copying into Applications. Both final moves are atomic
        # sibling renames; the old app remains intact during verification.
        sign_bundle(stage, run=run)
        if not read_work(databases, allow_missing=allow_missing)["idle"]:
            raise BusyInstall("Tracking or queued changes appeared while staging")
        _normal_quit(application, run=run, sleep=sleep)
        if app_running(application, run=run) or not read_work(databases, allow_missing=allow_missing)["idle"]:
            raise BusyInstall("New work appeared after normal quit")
        backup = _backup(databases, application, update_root, allow_missing=allow_missing)
        if app_running(application, run=run) or not read_work(databases, allow_missing=allow_missing)["idle"]:
            raise BusyInstall("Work appeared during backup; installation deferred")
        if application.exists():
            application.rename(previous)
            moved_previous = True
        stage.rename(application)
        installed_new = True
        verify_bundle(application, run=run)
        if relaunch:
            run(["open", str(application)], check=True, capture_output=True)
            for _ in range(40):
                if app_running(application, run=run):
                    break
                sleep(0.25)
            else:
                raise InstallError("Updated app did not launch")
        if moved_previous:
            shutil.rmtree(previous)
        return {"status": "installed", "application": str(application), "backup": str(backup),
                "relaunched": relaunch}
    except BaseException as error:
        # Roll back code only. Never replace live data with an older backup.
        if installed_new:
            if app_running(application, run=run):
                if not read_work(databases, allow_missing=allow_missing)["idle"]:
                    raise InstallError("Update failed after tracking resumed; current bundle and rollback copy were preserved") from error
                _normal_quit(application, run=run, sleep=sleep)
            if application.exists():
                shutil.rmtree(application)
        if moved_previous:
            previous.rename(application)
        if was_running and application.exists() and not app_running(application, run=run):
            run(["open", str(application)], check=True, capture_output=True)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _remove_job(config: dict, *, run=subprocess.run) -> None:
    Path(config["launch_agent"]).unlink(missing_ok=True)
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{UPDATE_LABEL}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def queue_update(replacement: Path, application: Path, databases, update_root: Path,
                 *, python=sys.executable, allow_missing=False, relaunch=True, run=subprocess.run) -> dict:
    python = shutil.which(str(python))
    if python is None:
        raise InstallError("Queued installer requires an executable Python interpreter")
    folder = update_root / "pending" / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    staged = folder / "JobTracker.app"
    shutil.copytree(replacement, staged, symlinks=True)
    verify_bundle(staged, run=run)
    installer = folder / "install_macos.py"
    shutil.copy2(Path(__file__), installer)
    installer.chmod(0o600)
    agent = Path.home() / "Library/LaunchAgents" / (UPDATE_LABEL + ".plist")
    agent.parent.mkdir(parents=True, exist_ok=True)
    config_path = folder / "config.json"
    config = {"replacement": str(staged), "application": str(application),
              "databases": [str(Path(path).expanduser().resolve()) for path in databases],
              "update_root": str(update_root), "launch_agent": str(agent),
              "previous_fingerprint": bundle_fingerprint(application),
              "replacement_fingerprint": bundle_fingerprint(staged),
              "allow_missing": allow_missing, "relaunch": relaunch}
    _write_json(config_path, config)
    payload = {"Label": UPDATE_LABEL, "ProgramArguments": [str(python), str(installer), "--pending", str(config_path)],
               "RunAtLoad": True, "StartInterval": 30, "ProcessType": "Background",
               "StandardOutPath": str(folder / "stdout.log"), "StandardErrorPath": str(folder / "stderr.log")}
    pointer = update_root / "pending.json"
    if pointer.exists():
        old_path = Path(json.loads(pointer.read_text())["config"])
        old_status = old_path.parent / "status.json"
        previous_status = json.loads(old_status.read_text()) if old_status.exists() else {}
        if previous_status.get("status") not in {"installed", "cancelled", "error", "superseded"}:
            _write_json(old_status, {"status": "superseded", "reason": "A newer staged update replaced this queue"})
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{UPDATE_LABEL}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    temporary = agent.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(payload))
    temporary.chmod(0o600)
    os.replace(temporary, agent)
    result = {"status": "queued", "config": str(config_path), "replacement": str(staged)}
    _write_json(folder / "status.json", result)
    _write_json(update_root / "pending.json", {"config": str(config_path)})
    try:
        run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        _write_json(folder / "status.json", {"status": "error", "reason": "Could not register idle installer"})
        agent.unlink(missing_ok=True)
        (update_root / "pending.json").unlink(missing_ok=True)
        raise
    return result


def cancel_pending(update_root=None, *, run=subprocess.run) -> dict:
    root = Path(update_root or Path.home() / "JobTracker-updates").expanduser().resolve()
    with _install_lock(root):
        pointer = root / "pending.json"
        if not pointer.exists():
            return {"status": "no_pending_update"}
        config_path = Path(json.loads(pointer.read_text())["config"])
        config = json.loads(config_path.read_text())
        result = {"status": "cancelled", "checked_at": datetime.now().isoformat()}
        _write_json(config_path.parent / "status.json", result)
        pointer.unlink(missing_ok=True)
        _remove_job(config, run=run)
        return result


def install_app(replacement, *, application=APPLICATION, databases=None, project=PROJECT_ROOT,
                update_root=None, python=sys.executable, queue=True, allow_missing=False,
                relaunch=True, run=subprocess.run, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    replacement = Path(replacement).expanduser().resolve()
    application = Path(application).expanduser().resolve()
    if replacement == application or application in replacement.parents or replacement in application.parents:
        raise InstallError("Staged and installed apps must be separate bundles")
    databases = default_databases(Path(project)) if databases is None else list(databases)
    update_root = Path(update_root or Path.home() / "JobTracker-updates").expanduser().resolve()
    verify_bundle(replacement, run=run)
    with _install_lock(update_root):
        deadline = monotonic() + STABLE_IDLE_SECONDS
        while True:
            work = read_work(databases, allow_missing=allow_missing)
            if not work["idle"]:
                if queue:
                    return queue_update(replacement, application, databases, update_root, python=python,
                                        allow_missing=allow_missing, relaunch=relaunch, run=run)
                raise BusyInstall("An open timer or pending outbox operation blocks installation")
            if monotonic() >= deadline:
                break
            sleep(min(1, max(0, deadline - monotonic())))
        try:
            return _install_ready(replacement, application, databases, update_root,
                                  allow_missing=allow_missing, relaunch=relaunch, run=run, sleep=sleep)
        except BusyInstall:
            if not queue:
                raise
            return queue_update(replacement, application, databases, update_root, python=python,
                                allow_missing=allow_missing, relaunch=relaunch, run=run)


def run_pending(config_path: Path, *, run=subprocess.run, now=time.time, sleep=time.sleep) -> dict:
    config = json.loads(config_path.read_text())
    folder = config_path.parent
    status_path = folder / "status.json"
    with _install_lock(Path(config["update_root"])):
        previous = json.loads(status_path.read_text()) if status_path.exists() else {}
        if previous.get("status") in {"installed", "superseded", "error", "cancelled"}:
            return previous
        try:
            application, replacement = Path(config["application"]), Path(config["replacement"])
            if bundle_fingerprint(application) != config["previous_fingerprint"]:
                result = {"status": "superseded", "reason": "Installed bundle changed; queued update cancelled"}
            else:
                work = read_work(config["databases"], allow_missing=config["allow_missing"])
                if not work["idle"]:
                    result = {"status": "waiting_for_idle", **work}
                else:
                    idle_since = previous.get("idle_since", now())
                    if now() - idle_since < STABLE_IDLE_SECONDS:
                        result = {"status": "waiting_for_idle", **work, "idle_since": idle_since}
                    else:
                        if bundle_fingerprint(replacement) != config["replacement_fingerprint"]:
                            raise InstallError("Queued staged bundle changed")
                        verify_bundle(replacement, run=run)
                        try:
                            result = _install_ready(replacement, application, config["databases"],
                                                    Path(config["update_root"]), allow_missing=config["allow_missing"],
                                                    relaunch=config["relaunch"], run=run, sleep=sleep)
                        except BusyInstall as error:
                            result = {"status": "waiting_for_idle", "reason": str(error)}
        except Exception as error:
            result = {"status": "error", "reason": str(error)}
        result["checked_at"] = datetime.now().isoformat()
        _write_json(status_path, result)
        if result["status"] in {"installed", "superseded", "error"}:
            pointer = Path(config["update_root"]) / "pending.json"
            if pointer.exists() and Path(json.loads(pointer.read_text())["config"]) == config_path:
                pointer.unlink()
            _remove_job(config, run=run)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staged_app", nargs="?")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--application", type=Path, default=APPLICATION)
    parser.add_argument("--database", type=Path, action="append")
    parser.add_argument("--update-root", type=Path)
    parser.add_argument("--no-queue", action="store_true")
    parser.add_argument("--no-relaunch", action="store_true")
    parser.add_argument("--allow-missing-databases", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pending", type=Path)
    parser.add_argument("--cancel-pending", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("JobTracker packaging is supported on macOS")
    try:
        if args.cancel_pending:
            result = cancel_pending(args.update_root)
        elif args.pending:
            result = run_pending(args.pending)
        elif args.status:
            result = inspect_install_state(args.application, args.database, project=args.project,
                                           allow_missing=args.allow_missing_databases)
        else:
            if not args.staged_app:
                parser.error("a staged app is required unless --status or --pending is used")
            result = install_app(args.staged_app, application=args.application, databases=args.database,
                                 project=args.project, update_root=args.update_root, python=args.python,
                                 queue=not args.no_queue, allow_missing=args.allow_missing_databases,
                                 relaunch=not args.no_relaunch)
        print(json.dumps(result, indent=2))
        return 1 if result.get("status") == "error" else 0
    except (InstallError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
