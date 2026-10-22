"""Build the native Mac app with its bundled engine, outside iCloud, and sign it.

Stages under ~/Library/Caches/JobTracker/builds/native-*:

1. Freeze ``jobtracker.desktop`` with PyInstaller (onedir, no Qt) into
   ``Contents/Resources/engine/`` (sealed as resources; every Mach-O inside is
   signed individually). A normal launch needs no system
   Python, checkout or download.
2. Build the SwiftUI app in release mode with SwiftPM.
3. Assemble the bundle, sign every nested Mach-O inside-out, then the app,
   and verify with ``codesign --verify --deep --strict``.
4. Smoke-test the frozen helper from a neutral folder on a disposable store.

The default ``pilot`` identity is a separate app (``com.jobtracker.native-pilot``)
on an isolated pilot store. ``production`` keeps the established identity
and Application Support store, and is only for the guarded cutover. This
script never installs anything.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SWIFT_SCRATCH = Path.home() / "Library/Caches/JobTracker/native-build"
IDENTITIES = {
    "pilot": {"bundle_id": "com.jobtracker.native-pilot", "name": "JobTracker Native Pilot",
              "mode": "isolated", "folder": "JobTracker Native Pilot"},
    "production": {"bundle_id": "com.jobtracker.app", "name": "JobTracker", "mode": "production", "folder": "JobTracker"},
}
ENGINE_EXCLUDES = ("PySide6", "shiboken6", "tkinter", "fastapi", "starlette", "uvicorn", "httpx", "pytest", "server")
MACH_O_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf"}


def _stage_root(build_root: Path | None) -> Path:
    root = Path(build_root or Path.home() / "Library/Caches/JobTracker/builds").expanduser().resolve()
    cloud = Path.home() / "Library/Mobile Documents"
    if root == PROJECT_ROOT or PROJECT_ROOT in root.parents or cloud == root or cloud in root.parents:
        raise ValueError("Native build staging must be outside the repository and iCloud Drive")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix="native-", dir=root))


def freeze_engine(python: str, stage: Path, run=subprocess.run) -> Path:
    source = stage / "engine-source"
    source.mkdir()
    shutil.copytree(PROJECT_ROOT / "jobtracker", source / "jobtracker",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "ui"))
    (source / "engine_main.py").write_text(
        "from jobtracker.desktop.__main__ import run\n\nif __name__ == \"__main__\":\n    run()\n")
    command = [python, "-m", "PyInstaller", "--noconfirm", "--onedir", "--console", "--name", "jobtracker-engine",
               "--distpath", str(stage / "engine-dist"), "--workpath", str(stage / "engine-work"),
               "--specpath", str(stage), "--paths", str(source), "--collect-submodules", "jobtracker",
               "--exclude-module", "jobtracker.ui", "--exclude-module", "jobtracker.sync.qt_worker"]
    for module in ENGINE_EXCLUDES:
        command += ["--exclude-module", module]
    run(command + [str(source / "engine_main.py")], cwd=source, check=True)
    helper = stage / "engine-dist/jobtracker-engine"
    if not (helper / "jobtracker-engine").is_file():
        raise RuntimeError("PyInstaller did not produce the engine helper")
    return helper


def build_swift(run=subprocess.run) -> Path:
    run(["swift", "build", "-c", "release", "--package-path", str(PROJECT_ROOT / "native/macos"),
         "--scratch-path", str(SWIFT_SCRATCH), "--product", "JobTracker"], check=True)
    binary = SWIFT_SCRATCH / "release/JobTracker"
    if not binary.is_file():
        raise RuntimeError("Swift build did not produce the JobTracker executable")
    return binary


def info_plist(identity: dict, version: str) -> dict:
    return {
        "CFBundleIdentifier": identity["bundle_id"],
        "CFBundleName": identity["name"],
        "CFBundleDisplayName": identity["name"],
        "CFBundleExecutable": "JobTracker",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "icon",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "JTEngineMode": identity["mode"],
        "JTStoreFolder": identity["folder"],
    }


def _is_mach_o(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACH_O_MAGIC
    except OSError:
        return False


def sign(application: Path, run=subprocess.run) -> None:
    run(["xattr", "-cr", str(application)], check=True, capture_output=True)
    nested = [path for path in (application / "Contents/Resources/engine").rglob("*")
              if path.is_file() and not path.is_symlink() and _is_mach_o(path)]
    # Inside-out: deepest code first, the app bundle last.
    for path in sorted(nested, key=lambda item: len(item.parts), reverse=True):
        run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(path)], check=True, capture_output=True)
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(application)], check=True, capture_output=True)
    run(["codesign", "--verify", "--deep", "--strict", str(application)], check=True, capture_output=True)


def assemble(stage: Path, binary: Path, helper: Path, identity_name: str, version: str) -> Path:
    identity = IDENTITIES[identity_name]
    application = stage / "dist" / f"{identity['name']}.app"
    contents = application / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(binary, contents / "MacOS/JobTracker")
    shutil.copytree(helper, contents / "Resources/engine", symlinks=True)
    shutil.copy2(PROJECT_ROOT / "assets/icon.icns", contents / "Resources/icon.icns")
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info_plist(identity, version), handle)
    (contents / "PkgInfo").write_text("APPL????")
    return application


def smoke_helper(application: Path, run=subprocess.run) -> dict:
    """Run the frozen helper from a neutral folder on a disposable store."""
    helper = application / "Contents/Resources/engine/jobtracker-engine"
    with tempfile.TemporaryDirectory(prefix="jobtracker-helper-smoke-") as directory:
        env = {"HOME": os.environ.get("HOME", directory), "PATH": "/usr/bin:/bin",
               "JOBTRACKER_LOCK_DIR": str(Path(directory) / "locks")}
        frame = json.dumps({"protocol_version": 1, "kind": "command", "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                            "command": "engine.info", "params": {}}) + "\n"
        result = run([str(helper), "--mode", "isolated", "--db", str(Path(directory) / "smoke.db")], input=frame,
                     capture_output=True, text=True, cwd=directory, env=env, timeout=120)
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not lines or lines[0].get("kind") != "hello":
            raise RuntimeError(f"Frozen engine smoke test failed ({result.returncode}): {result.stderr[-800:]}")
        info = next((line for line in lines if line.get("kind") == "response"), {})
        if info.get("outcome") != "ok" or info["result"].get("qt_loaded"):
            raise RuntimeError("Frozen engine answered incorrectly or loaded Qt")
        return {"hello": True, "qt_loaded": False, "commands": len(lines[0].get("commands", []))}


def architectures(path: Path, run=subprocess.run) -> str:
    return run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def build(python: str, *, identity: str = "pilot", version: str = "2.0.0", build_root: Path | None = None) -> dict:
    if identity not in IDENTITIES:
        raise ValueError(f"identity must be one of {', '.join(IDENTITIES)}")
    stage = _stage_root(build_root)
    helper = freeze_engine(python, stage)
    binary = build_swift()
    application = assemble(stage, binary, helper, identity, version)
    sign(application)
    smoke = smoke_helper(application)
    size = sum(path.stat().st_size for path in application.rglob("*") if path.is_file() and not path.is_symlink())
    return {
        "status": "built",
        "identity": IDENTITIES[identity]["bundle_id"],
        "staged_app": str(application),
        "app_arch": architectures(application / "Contents/MacOS/JobTracker"),
        "helper_arch": architectures(application / "Contents/Resources/engine/jobtracker-engine"),
        "size_mb": round(size / 1048576, 1),
        "helper_smoke": smoke,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=os.environ.get("JOBTRACKER_PYTHON", sys.executable))
    parser.add_argument("--identity", choices=sorted(IDENTITIES), default="pilot")
    parser.add_argument("--version", default="2.0.0")
    parser.add_argument("--build-root", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("JobTracker packaging is supported on macOS")
    try:
        print(json.dumps(build(args.python, identity=args.identity, version=args.version, build_root=args.build_root), indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
