"""One entry point for feature context, checks, tests and safe release commands."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    from .source_index import ROOT
except ImportError:
    from source_index import ROOT


def feature_context(ident: str) -> dict:
    features = json.loads((ROOT / "docs/architecture/features.json").read_text())["features"]
    try:
        return next(feature for feature in features if feature["id"] == ident)
    except StopIteration:
        raise ValueError(f"Unknown feature {ident}. Available: " + ", ".join(feature["id"] for feature in features)) from None


def find_features(query: str = "") -> list[dict]:
    """Search only the small registry; never crawl/import application code."""
    features = json.loads((ROOT / "docs/architecture/features.json").read_text())["features"]
    exact = [feature for feature in features if feature["id"] == query]
    if exact:
        return exact
    words = re.findall(r"[a-z0-9]+", query.lower())
    matches = []
    for feature in features:
        # Related IDs/contracts are deliberately excluded: their matches would
        # send a targeted query to almost every feature in the application.
        searchable = {key: feature.get(key) for key in
                      ("id", "title", "keywords", "entrypoints", "supporting", "rule_owner", "persistence", "operations", "routes", "tables")}
        text = json.dumps(searchable).lower()
        if all(word in text for word in words):
            matches.append(feature)
    return matches


def print_context(feature: dict):
    print(f"{feature['id']}: {feature['title']}")
    for label, references in (("Entry points", feature["entrypoints"]), ("Rule owner", [feature["rule_owner"]]), ("Persistence / adapter", feature["persistence"])):
        print(label + ": " + ", ".join(reference["path"] + ("::" + reference["symbol"] if reference.get("symbol") else "") for reference in references))
    if feature.get("supporting"):
        print("Supporting files: " + ", ".join(reference["path"] + ("::" + reference["symbol"] if reference.get("symbol") else "") for reference in feature["supporting"]))
    if feature.get("guide"):
        print("Task guide: " + feature["guide"])
    contracts = json.loads((ROOT / "docs/architecture/contracts.json").read_text())["contracts"]
    print("Contracts: " + ", ".join(contract["document"] for contract in contracts if contract["id"] in feature["contracts"]))
    print("Operations: " + (", ".join(feature["operations"]) or "None (read/presentation)"))
    print("Routes: " + (", ".join(feature["routes"]) or "None"))
    print("Tables: " + (", ".join(feature["tables"]) or "None"))
    python_tests = [path for path in feature["tests"] if path.endswith(".py")]
    node_tests = [path for path in feature["tests"] if path.endswith(".mjs")]
    if python_tests:
        print("Focused Python: python scripts/dev.py test " + feature["id"] + " --python /path/to/python")
    if node_tests:
        print("Focused phone: node --test " + " ".join(node_tests))
    for path in feature["tests"]:
        if path.endswith(".cjs"):
            print("Browser: node " + path)
    print("Test files: " + ", ".join(feature["tests"]))
    print("Related: " + ", ".join(feature["related"]))
    print("Synced writes: jobtracker/sync/service.py or web/data/outbox.js → POST /ops; see docs/architecture/write-path.md")
    print("After moving an owner: update features.json; run python scripts/dev.py check --write, then check.")


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT).returncode


def validate_python(executable: str, dependencies: list[str]) -> str:
    command = [executable, "-c", "import importlib.util,json,sys; print(json.dumps({'version':list(sys.version_info[:2]),'missing':[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]}))", *dependencies]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ValueError(f"Cannot use Python interpreter {executable}: {exc}") from None
    if info["version"] < [3, 10]:
        raise ValueError("Python 3.10 or newer is required.")
    if info["missing"]:
        raise ValueError("Missing dependencies in selected interpreter: " + ", ".join(info["missing"]) + ". Install requirements-dev.txt for the complete test suite.")
    return executable


def status() -> int:
    paths = [ROOT / "data/jobtracker.db", Path.home() / "Library/Application Support/JobTracker/jobtracker.db"]
    for label, path in zip(("Development database", "Installed database"), paths):
        if not path.is_file():
            print(label + ": absent")
            continue
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as database:
                tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                opened = database.execute("SELECT count(*) FROM sessions WHERE end_time IS NULL").fetchone()[0] if "sessions" in tables else "unknown"
                pending = database.execute("SELECT count(*) FROM sync_outbox").fetchone()[0] if "sync_outbox" in tables else 0
                print(f"{label}: {opened} open sessions; {pending} pending operations")
        except sqlite3.Error as exc:
            print(f"{label}: unreadable ({type(exc).__name__}); installation must wait")
            return 1
    if sys.platform == "darwin":
        result = subprocess.run(["pgrep", "-f", r"JobTracker\.app/Contents/MacOS/JobTracker"], capture_output=True, text=True)
        print("Packaged processes: " + str(len(result.stdout.splitlines())))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter for tests/builds (defaults to this interpreter).")
    sub = parser.add_subparsers(dest="command", required=True)
    context_parser = sub.add_parser("context", help="Print a compact feature task packet.")
    context_parser.add_argument("query", nargs="*", help="Feature ID, words, or file path; omit to list features.")
    context_parser.add_argument("--json", action="store_true", help="Emit matching registry entries without prose.")
    check_parser = sub.add_parser("check", help="Check references, imports and web assets without importing the app.")
    check_parser.add_argument("--write", action="store_true", help="Regenerate source-derived reference maps.")
    tests = sub.add_parser("test", help="Run the full suite, or a feature's Python tests.")
    tests.add_argument("feature", nargs="?")
    tests.add_argument("--python", default=argparse.SUPPRESS)
    sub.add_parser("mobile", help="Run all dependency-free Node contract tests.")
    sub.add_parser("browser", help="Run the optional Playwright browser harness.")
    build = sub.add_parser("build", help="Build and sign outside iCloud; installation is explicit and guarded.")
    build.add_argument("--python", default=argparse.SUPPRESS)
    build.add_argument("--install", action="store_true", help="Install now when idle, otherwise queue a guarded idle installation.")
    build.add_argument("--keep-dist", action="store_true")
    build.add_argument("--setup", action="store_true", help="Explicitly install required build dependencies.")
    sub.add_parser("status", help="Read active-session/outbox status without changing data.")
    sub.add_parser("deploy", help="Deploy to the configured private server using its existing installer.")
    args = parser.parse_args(argv)
    try:
        if args.command == "context":
            query = " ".join(args.query)
            matches = find_features(query)
            if not matches:
                raise ValueError(f"No feature matches {query!r}. Run context without a query to list features.")
            if args.json:
                print(json.dumps(matches, indent=2))
            elif len(matches) == 1:
                print_context(matches[0])
            else:
                for feature in matches:
                    print(f"{feature['id']}: {feature['title']}")
                print("Use context FEATURE_ID for owners, supporting files, contracts and tests.")
            return 0
        if args.command == "check":
            try:
                from . import check_references, check_boundaries, check_web_assets
            except ImportError:
                import check_references, check_boundaries, check_web_assets
            results = [check_references.check(ROOT, write=args.write), check_boundaries.check(ROOT), check_web_assets.check(ROOT)]
            errors = [error for result in results for error in result]
            print("\n".join(errors) if errors else "References, layer boundaries and offline assets pass.")
            return int(bool(errors))
        if args.command == "test":
            paths = [path for path in feature_context(args.feature)["tests"] if path.endswith(".py")] if args.feature else []
            if args.feature and not paths:
                raise ValueError("This feature has no Python tests; use the phone commands in its context packet.")
            dependencies = ["pytest", "PySide6", "fastapi", "httpx"]
            executable = validate_python(args.python, dependencies)
            return run([executable, "-m", "pytest", "-o", "addopts=", "-q", *paths])
        if args.command == "mobile":
            return run(["node", "--test", *[str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests/mobile").glob("*.test.mjs"))]])
        if args.command == "browser":
            for harness in ("tests/mobile/browser.cjs", "tests/mobile/feature-sheets.cjs",
                            "tests/mobile/subject-quick-add.cjs", "tests/mobile/accessibility.cjs"):
                result = run(["node", harness])
                if result:
                    return result
            return 0
        if args.command == "build":
            executable = validate_python(args.python, [] if args.setup else ["PyInstaller", "PySide6"])
            command = [executable, str(ROOT / "scripts/build_macos.py"), "--python", executable]
            if not args.install:
                command.append("--no-install")
            for flag in ("keep_dist", "setup"):
                if getattr(args, flag):
                    command.append("--" + flag.replace("_", "-"))
            return run(command)
        if args.command == "status":
            return status()
        if args.command == "deploy":
            return run(["bash", "deploy/install.sh"])
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
