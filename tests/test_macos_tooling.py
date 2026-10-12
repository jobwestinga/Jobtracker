"""Install/build failure contracts using only synthetic data and fake commands."""

from pathlib import Path
from types import SimpleNamespace
import json
import plistlib
import sqlite3
import struct
import subprocess
import sys

import pytest

from scripts import build_macos, install_macos
from scripts.build_icon import ICON_SIZES, encode_icns


def bundle(path, content=b"old"):
    executable = path / "Contents/MacOS/JobTracker"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(content)
    executable.chmod(0o755)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": install_macos.APP_ID}))
    return path


class MacCommands:
    def __init__(self, application):
        self.application = application
        self.commands = []
        self.running = False
        self.refuse_quit = False
        self.fail_final_verify = False
        self.fail_open = False

    def __call__(self, command, **options):
        self.commands.append(command)
        if command[0] == "pgrep":
            return SimpleNamespace(returncode=0 if self.running else 1)
        if command[0] == "osascript" and not self.refuse_quit:
            self.running = False
        if command[0] == "open":
            if self.fail_open:
                raise subprocess.CalledProcessError(1, command)
            self.running = True
        if command[:2] == ["codesign", "--verify"] and Path(command[-1]) == self.application and self.fail_final_verify:
            self.fail_final_verify = False
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def world(tmp_path, database, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(install_macos, "STABLE_IDLE_SECONDS", 0)
    application = bundle(tmp_path / "Applications/JobTracker.app")
    replacement = bundle(tmp_path / "staged/JobTracker.app", b"new")
    runner = MacCommands(application)
    options = {"application": application, "databases": [Path(database.db_path)],
               "update_root": home / "updates", "run": runner, "sleep": lambda _: None,
               "relaunch": False}
    return SimpleNamespace(home=home, database=database, application=application,
                           replacement=replacement, runner=runner, options=options)


def test_missing_required_database_fails_closed_and_never_creates_file(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(install_macos.InstallError, match="Required database"):
        install_macos.read_work([missing])
    assert not missing.exists()
    assert install_macos.read_work([missing], allow_missing=True)["idle"]


@pytest.mark.parametrize("contents", [b"not sqlite", b""])
def test_unreadable_or_wrong_required_database_fails_closed(tmp_path, contents):
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(contents)
    with pytest.raises(install_macos.InstallError):
        install_macos.read_work([invalid])
    assert invalid.read_bytes() == contents


def test_all_devices_open_timers_and_all_outbox_rows_block_install(world):
    db = world.database
    subject = db.add_subject("Foreign device", "#123456", "")
    db.connection.execute("INSERT INTO sessions(task_id,start_time,device_id) VALUES(?,?,'phone')",
                          (subject.id, "2026-10-11T09:00:00"))
    db.connection.execute("CREATE TABLE sync_outbox(id INTEGER PRIMARY KEY, payload TEXT)")
    db.connection.execute("INSERT INTO sync_outbox(payload) VALUES('private operation')")
    db.connection.commit()
    before = db.export_data()
    work = install_macos.read_work([db.db_path, db.db_path])
    assert work["active_sessions"] == work["pending_operations"] == 1
    assert not work["idle"]
    assert "private operation" not in json.dumps(work)
    assert db.export_data() == before
    with pytest.raises(install_macos.BusyInstall):
        install_macos.install_app(world.replacement, queue=False, **world.options)
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    assert not any(command[0] == "osascript" for command in world.runner.commands)


def test_idle_install_has_verified_stage_backup_and_keeps_data(world):
    world.database.add_subject("Stored", "#123456", "")
    before = world.database.export_data()
    result = install_macos.install_app(world.replacement, **world.options)
    assert result["status"] == "installed"
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"new"
    assert (world.replacement / "Contents/MacOS/JobTracker").read_bytes() == b"new"
    backup = Path(result["backup"])
    assert (backup / "PreviousJobTracker.app/Contents/MacOS/JobTracker").read_bytes() == b"old"
    with sqlite3.connect(backup / "database-1.db") as db:
        assert db.execute("SELECT name FROM tasks").fetchone()[0] == "Stored"
    assert (backup / "database-1.db").stat().st_mode & 0o777 == 0o600
    assert world.database.export_data() == before
    signed = [Path(command[-1]) for command in world.runner.commands if command[:2] == ["codesign", "--force"]]
    assert len(signed) == 1 and signed[0].parent == world.application.parent
    assert not list(world.application.parent.glob(".JobTracker-*.app"))


def test_final_verification_failure_rolls_back_code_without_data_restore(world):
    world.runner.fail_final_verify = True
    world.database.add_subject("Must survive", "#123456", "")
    before = world.database.export_data()
    with pytest.raises(subprocess.CalledProcessError):
        install_macos.install_app(world.replacement, **world.options)
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    assert world.database.export_data() == before
    assert list((world.home / "updates/backups").glob("*/PreviousJobTracker.app"))


def test_refused_normal_quit_never_replaces_or_force_kills_app(world):
    world.runner.running = world.runner.refuse_quit = True
    with pytest.raises(install_macos.BusyInstall, match="Normal quit"):
        install_macos.install_app(world.replacement, queue=False, **world.options)
    assert world.runner.running
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    assert not any(command[0] in {"kill", "killall"} for command in world.runner.commands)


def test_work_appearing_during_staging_preserves_installed_app(world):
    original = world.runner

    def run(command, **options):
        result = original(command, **options)
        if command[:2] == ["codesign", "--force"]:
            subject = world.database.add_subject("New timer", "#123456", "")
            world.database.connection.execute("INSERT INTO sessions(task_id,start_time) VALUES(?,?)",
                                              (subject.id, "2026-10-11T09:00:00"))
            world.database.connection.commit()
        return result

    options = {**world.options, "run": run, "queue": False}
    with pytest.raises(install_macos.BusyInstall, match="staging"):
        install_macos.install_app(world.replacement, **options)
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    assert install_macos.read_work([world.database.db_path])["active_sessions"] == 1


def test_busy_install_is_durable_and_cancellable_without_bundle_changes(world):
    subject = world.database.add_subject("Active", "#123456", "")
    world.database.connection.execute("INSERT INTO sessions(task_id,start_time) VALUES(?,?)", (subject.id, "2026-10-11T09:00:00"))
    world.database.connection.commit()
    result = install_macos.install_app(world.replacement, **world.options)
    assert result["status"] == "queued"
    config_path = Path(result["config"])
    config = json.loads(config_path.read_text())
    agent = plistlib.loads(Path(config["launch_agent"]).read_bytes())
    assert agent["ProgramArguments"][0] == sys.executable
    assert Path(agent["ProgramArguments"][1]).is_file()
    assert agent["ProgramArguments"][-2:] == ["--pending", str(config_path)]
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    cancelled = install_macos.cancel_pending(world.home / "updates", run=world.runner)
    assert cancelled["status"] == "cancelled"
    assert not Path(config["launch_agent"]).exists()
    assert install_macos.run_pending(config_path, run=world.runner)["status"] == "cancelled"


def test_pending_waits_for_continuous_idle_and_staged_signature(world, monkeypatch):
    config_result = install_macos.queue_update(world.replacement, world.application,
                                             [world.database.db_path], world.home / "updates",
                                             run=world.runner, relaunch=False)
    config_path = Path(config_result["config"])
    monkeypatch.setattr(install_macos, "STABLE_IDLE_SECONDS", 25)
    assert install_macos.run_pending(config_path, run=world.runner, now=lambda: 100)["status"] == "waiting_for_idle"
    assert install_macos.run_pending(config_path, run=world.runner, now=lambda: 124)["status"] == "waiting_for_idle"
    result = install_macos.run_pending(config_path, run=world.runner, now=lambda: 126)
    assert result["status"] == "installed"


def test_pending_cancels_when_installed_bundle_was_superseded(world):
    queued = install_macos.queue_update(world.replacement, world.application,
                                       [world.database.db_path], world.home / "updates", run=world.runner)
    (world.application / "Contents/MacOS/JobTracker").write_bytes(b"another update")
    result = install_macos.run_pending(Path(queued["config"]), run=world.runner)
    assert result["status"] == "superseded"
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"another update"


def test_process_inspection_failure_is_fail_closed(world):
    def failed(command, **options):
        if command[0] == "pgrep":
            return SimpleNamespace(returncode=2)
        return world.runner(command, **options)

    with pytest.raises(install_macos.InstallError, match="process"):
        install_macos.install_app(world.replacement, **{**world.options, "run": failed})
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"


def test_installer_rejects_live_bundle_as_its_source(world):
    with pytest.raises(install_macos.InstallError, match="separate"):
        install_macos.install_app(world.application, **world.options)


def test_source_snapshot_excludes_data_and_preserves_originals(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('synthetic')")
    (project / "JobTracker.spec").write_text("# synthetic spec")
    (project / "jobtracker").mkdir()
    (project / "jobtracker/app.py").write_text("# synthetic source")
    (project / "jobtracker/__pycache__").mkdir()
    (project / "jobtracker/__pycache__/secret.pyc").write_bytes(b"skip")
    (project / "assets").mkdir()
    (project / "assets/icon.icns").write_bytes(b"keep")
    (project / "data").mkdir()
    (project / "data/private.db").write_bytes(b"private")
    stage = tmp_path / "stage"
    stage.mkdir()
    source = build_macos.prepare_source(project, stage)
    assert not (source / "data").exists()
    assert not (source / "jobtracker/__pycache__").exists()
    (source / "assets/icon.icns").write_bytes(b"staged change")
    assert (project / "assets/icon.icns").read_bytes() == b"keep"


def test_build_rejects_staging_inside_repository(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        build_macos.build(tmp_path, build_root=tmp_path / "build")


def test_icns_chunks_have_correct_sizes_and_all_original_resolutions():
    images = {size: b"synthetic PNG" + str(size).encode() for _, size in ICON_SIZES}
    data = encode_icns(images)
    assert data[:4] == b"icns"
    assert struct.unpack(">I", data[4:8])[0] == len(data)
    offset = 8
    for kind, size in ICON_SIZES:
        assert data[offset:offset + 4] == kind.encode()
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        assert data[offset + 8:offset + length] == images[size]
        offset += length
    assert offset == len(data)


def test_running_dist_copy_is_preserved_and_new_signed_stage_remains(world):
    project = world.home / "project"
    project.mkdir()
    destination = bundle(project / "dist/JobTracker.app", b"running dist")
    commands = MacCommands(destination)
    commands.running = True
    result = build_macos.retain_dist_app(world.replacement, project, run=commands)
    assert result["dist_preserved"] == "Existing dist app is running"
    assert (destination / "Contents/MacOS/JobTracker").read_bytes() == b"running dist"
    assert (world.replacement / "Contents/MacOS/JobTracker").read_bytes() == b"new"
    assert not any(command[0] in {"osascript", "open"} for command in commands.commands)


def test_dist_copy_starting_during_stage_is_also_preserved(world, monkeypatch):
    project = world.home / "project"
    project.mkdir()
    destination = bundle(project / "dist/JobTracker.app", b"running dist")
    values = iter((False, True))
    monkeypatch.setattr(build_macos, "app_running", lambda *args, **kwargs: next(values))
    result = build_macos.retain_dist_app(world.replacement, project, run=world.runner)
    assert result["dist_preserved"] == "Dist app started while copying"
    assert (destination / "Contents/MacOS/JobTracker").read_bytes() == b"running dist"
    assert not list(destination.parent.glob(".JobTracker-*.app"))


def test_pending_tampered_source_is_rejected_before_replacement(world, monkeypatch):
    monkeypatch.setattr(install_macos, "STABLE_IDLE_SECONDS", 25)
    queued = install_macos.queue_update(world.replacement, world.application, [world.database.db_path],
                                       world.home / "updates", run=world.runner)
    config_path = Path(queued["config"])
    install_macos.run_pending(config_path, run=world.runner, now=lambda: 100)
    config = json.loads(config_path.read_text())
    (Path(config["replacement"]) / "Contents/MacOS/JobTracker").write_bytes(b"tampered")
    result = install_macos.run_pending(config_path, run=world.runner, now=lambda: 130)
    assert result["status"] == "error"
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"old"
    assert not (world.home / "updates/pending.json").exists()


def test_new_queue_supersedes_old_and_cancel_only_targets_current(world):
    first = install_macos.queue_update(world.replacement, world.application, [world.database.db_path],
                                      world.home / "updates", run=world.runner)
    second = install_macos.queue_update(world.replacement, world.application, [world.database.db_path],
                                       world.home / "updates", run=world.runner)
    first_path, second_path = Path(first["config"]), Path(second["config"])
    assert install_macos.run_pending(first_path, run=world.runner)["status"] == "superseded"
    assert Path(json.loads(second_path.read_text())["launch_agent"]).exists()
    install_macos.cancel_pending(world.home / "updates", run=world.runner)
    assert install_macos.run_pending(second_path, run=world.runner)["status"] == "cancelled"


def test_build_only_honors_environment_without_install_or_dependency_changes(world, monkeypatch, capsys):
    project = world.home / "project"
    project.mkdir()
    monkeypatch.setenv("INSTALL_TO_APPLICATIONS", "0")
    monkeypatch.setenv("KEEP_DIST_APP", "0")
    monkeypatch.setattr(build_macos, "validate_interpreter", lambda value: sys.executable)
    monkeypatch.setattr(build_macos, "build", lambda *args, **kwargs: world.replacement)
    monkeypatch.setattr(build_macos, "install_app", lambda *args, **kwargs: pytest.fail("build-only must not install"))
    assert build_macos.main(["--project", str(project)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["staged_app"] == str(world.replacement)
    assert Path(result["dist_app"]).exists()


def test_pending_idle_period_resets_if_any_device_starts_work(world, monkeypatch):
    monkeypatch.setattr(install_macos, "STABLE_IDLE_SECONDS", 25)
    queued = install_macos.queue_update(world.replacement, world.application, [world.database.db_path],
                                       world.home / "updates", run=world.runner, relaunch=False)
    path = Path(queued["config"])
    assert install_macos.run_pending(path, run=world.runner, now=lambda: 100)["idle_since"] == 100
    subject = world.database.add_subject("Interruption", "#123456", "")
    world.database.connection.execute("INSERT INTO sessions(task_id,start_time,device_id) VALUES(?,?,'phone')",
                                      (subject.id, "2026-10-11T09:00:00"))
    world.database.connection.commit()
    assert "idle_since" not in install_macos.run_pending(path, run=world.runner, now=lambda: 110)
    world.database.connection.execute("DELETE FROM sessions")
    world.database.connection.commit()
    assert install_macos.run_pending(path, run=world.runner, now=lambda: 120)["idle_since"] == 120
    assert install_macos.run_pending(path, run=world.runner, now=lambda: 130)["status"] == "waiting_for_idle"
    assert install_macos.run_pending(path, run=world.runner, now=lambda: 146)["status"] == "installed"


def test_rollback_never_quits_newly_started_work(world):
    original = world.runner

    def run(command, **options):
        if command[:2] == ["codesign", "--verify"] and Path(command[-1]) == world.application:
            subject = world.database.add_subject("Started during verification", "#123456", "")
            world.database.connection.execute("INSERT INTO sessions(task_id,start_time) VALUES(?,?)",
                                              (subject.id, "2026-10-11T09:00:00"))
            world.database.connection.commit()
            original.running = True
            raise subprocess.CalledProcessError(1, command)
        return original(command, **options)

    with pytest.raises(install_macos.InstallError, match="tracking resumed"):
        install_macos.install_app(world.replacement, **{**world.options, "run": run})
    assert original.running
    assert install_macos.read_work([world.database.db_path])["active_sessions"] == 1
    assert (world.application / "Contents/MacOS/JobTracker").read_bytes() == b"new"
    assert list(world.application.parent.glob(".JobTracker-previous-*.app"))
    assert not any(command[0] == "osascript" for command in original.commands)


def test_build_runs_selected_python_in_private_source_stage(world):
    project = world.home / "project"
    project.mkdir()
    (project / "main.py").write_text("# synthetic main")
    (project / "JobTracker.spec").write_text("# synthetic spec")
    (project / "jobtracker").mkdir()
    (project / "jobtracker/app.py").write_text("# synthetic app")
    (project / "assets").mkdir()
    commands = []

    def run(command, **options):
        commands.append(command)
        if command[1:3] == ["-m", "PyInstaller"]:
            destination = Path(command[command.index("--distpath") + 1])
            bundle(destination / "JobTracker.app", b"compiled synthetic")
            assert Path(options["cwd"]) != project
        return SimpleNamespace(returncode=0)

    built = build_macos.build(project, python=sys.executable, build_root=world.home / "cache", run=run)
    assert (built / "Contents/MacOS/JobTracker").read_bytes() == b"compiled synthetic"
    assert not (project / "build").exists()
    assert not (project / "dist").exists()
    assert commands[0][0] == sys.executable
    assert not any("pip" in command for command in commands)


def test_empty_guard_list_cannot_disable_required_database_checks():
    with pytest.raises(install_macos.InstallError, match="required database"):
        install_macos.read_work([])
