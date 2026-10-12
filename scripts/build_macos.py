"""Build outside iCloud, verify a signed bundle, then use the guarded installer."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.install_macos import app_running, install_app, sign_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def validate_interpreter(python: str, *, require_build_modules=True, run=subprocess.run) -> str:
    executable = shutil.which(python)
    if executable is None:
        raise ValueError(f"Python interpreter is not executable: {python}")
    code = "import sys,json; assert sys.version_info >= (3,10); "
    if require_build_modules:
        code += "import PyInstaller,PySide6; "
    code += "print(json.dumps(sys.executable))"
    try:
        result = run([executable, "-c", code], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise ValueError("Selected Python needs Python 3.10+, PyInstaller and PySide6. Use --setup explicitly to install build dependencies.") from error
    return json.loads(result.stdout.strip())


def prepare_source(project: Path, stage: Path) -> Path:
    source = stage / "source"
    source.mkdir()
    for filename in ("main.py", "JobTracker.spec"):
        shutil.copy2(project / filename, source / filename)
    shutil.copytree(project / "jobtracker", source / "jobtracker",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    shutil.copytree(project / "assets", source / "assets", symlinks=True,
                    ignore=shutil.ignore_patterns(".DS_Store"))
    return source


def build(project=PROJECT_ROOT, *, python=sys.executable, build_root=None, run=subprocess.run) -> Path:
    project = Path(project).expanduser().resolve()
    root = Path(build_root or Path.home() / "Library/Caches/JobTracker/builds").expanduser().resolve()
    cloud = Path.home() / "Library/Mobile Documents"
    if root == project or project in root.parents or root == cloud or cloud in root.parents:
        raise ValueError("Build staging must be outside the repository and iCloud Drive")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix="build-", dir=root))
    source = prepare_source(project, stage)
    artwork = source / "assets/icon.png"
    if not artwork.exists() and (project / "JobTracker.png").exists():
        artwork = source / "JobTracker.png"
        shutil.copy2(project / "JobTracker.png", artwork)
    if artwork.exists():
        try:
            icon_tool = stage / "build_icon.py"
            shutil.copy2(Path(__file__).with_name("build_icon.py"), icon_tool)
            run([python, str(icon_tool), str(artwork), str(source / "assets/icon.icns")], check=True)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            print(f"Icon generation failed; retaining the staged existing icon: {error}", file=sys.stderr)
    run([python, "-m", "PyInstaller", "JobTracker.spec", "--noconfirm",
         "--distpath", str(stage / "dist"), "--workpath", str(stage / "work")],
        cwd=source, check=True)
    application = stage / "dist/JobTracker.app"
    sign_bundle(application, run=run)
    return application


def retain_dist_app(application: Path, project: Path, *, run=subprocess.run) -> dict:
    destination = project / "dist/JobTracker.app"
    # A developer can launch the retained copy. Preserve it while running;
    # the newly signed cache bundle is always available for a later install.
    if destination.parent.is_symlink() or destination.is_symlink():
        return {"dist_preserved": "Existing dist path is a symlink", "dist_app": str(destination)}
    if destination.exists() and app_running(destination, run=run):
        return {"dist_preserved": "Existing dist app is running", "dist_app": str(destination)}
    destination.parent.mkdir(exist_ok=True)
    candidate = destination.parent / (".JobTracker-staged-" + uuid.uuid4().hex + ".app")
    previous = destination.parent / (".JobTracker-previous-" + uuid.uuid4().hex + ".app")
    try:
        shutil.copytree(application, candidate, symlinks=True)
        if destination.exists() and app_running(destination, run=run):
            return {"dist_preserved": "Dist app started while copying", "dist_app": str(destination)}
        if destination.exists():
            destination.rename(previous)
        try:
            candidate.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
        if previous.exists():
            shutil.rmtree(previous)
        return {"dist_app": str(destination)}
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=os.environ.get("JOBTRACKER_PYTHON", sys.executable))
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument("--keep-dist", action="store_true")
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("JobTracker packaging is supported on macOS")
    try:
        candidate = args.python
        if args.setup:
            bootstrap = validate_interpreter(candidate, require_build_modules=False)
            environment = Path.home() / "Library/Caches/JobTracker/build-environment"
            if not (environment / "bin/python3").exists():
                subprocess.run([bootstrap, "-m", "venv", str(environment)], check=True)
            candidate = str(environment / "bin/python3")
            subprocess.run([candidate, "-m", "pip", "install", "-r", str(args.project / "requirements.txt")], check=True)
        python = validate_interpreter(candidate)
        application = build(args.project, python=python, build_root=args.build_root)
        install = not args.no_install and os.environ.get("INSTALL_TO_APPLICATIONS", "1") == "1"
        keep = args.keep_dist or os.environ.get("KEEP_DIST_APP", "0") == "1" or not install
        result = {"status": "built", "staged_app": str(application)}
        if install:
            result["install"] = install_app(application, project=args.project, python=python)
        if keep:
            result.update(retain_dist_app(application, args.project))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
