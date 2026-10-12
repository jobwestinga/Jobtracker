"""Produce a dated refactor/performance snapshot using source and synthetic data."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "607f5ddac2d46ccfc731d3805cd1784bb9a6d732"
FILES = ("web/app.js", "jobtracker/core/database.py", "jobtracker/services/tracker_service.py",
         "jobtracker/ui/app.py", "server/app.py", "server/ops.py", "jobtracker/sync/engine.py",
         "tests/test_ui_smoke.py")


def browser_result(path: Path) -> dict:
    for line in reversed(path.read_text().splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if result.get("browserChecks") == "passed" and "pickerPerformance" in result:
            # Only synthetic metrics enter the report; omit paths, requests,
            # screenshots, credentials or other fields from a supplied log.
            return {"viewports": result.get("viewports", []), "performance": result["pickerPerformance"]}
    raise ValueError("No complete passing browser performance result found")


def math_benchmark(rounds: int) -> dict:
    sys.path.insert(0, str(ROOT))
    from jobtracker.core.weight_plot import points_for, trend_for
    start = date(2023, 1, 1)
    rows = [{"uid": f"synthetic-{index}", "measured_at": (start + timedelta(days=index)).isoformat() + "T00:00:00",
             "weight_kg": 80 + (index % 17) / 10, "source": "manual"} for index in range(1060)]
    elapsed = []
    for _ in range(rounds):
        before = time.perf_counter()
        points = points_for(rows)
        trend_for(points, mode="loess", span=0.04)
        elapsed.append((time.perf_counter() - before) * 1000)
    return {"rows": len(rows), "rounds": rounds, "median_ms": statistics.median(elapsed), "max_ms": max(elapsed)}


def report(baseline: str, metrics: dict, browser=None) -> str:
    rows = ["# Refactor measurement snapshot", "", f"Measured {date.today().isoformat()} against baseline `{baseline}`.", "",
            "Counts measure the review surface of composition/facade files. Implementations moved into feature owners; compatibility wrappers and focused tests add code elsewhere.", "",
            "| File | Baseline lines | Current lines |", "| --- | ---: | ---: |"]
    for filename in FILES:
        result = subprocess.run(["git", "show", f"{baseline}:{filename}"], cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Baseline file unavailable: {filename}")
        current = ROOT / filename
        rows.append(f"| `{filename}` | {len(result.stdout.splitlines())} | {len(current.read_text().splitlines()) if current.exists() else 'Split into tests/ui groups'} |")
    rows += ["", "## Synthetic weight mathematics", "",
             f"Python point selection plus LOESS, {metrics['rows']} daily readings, {metrics['rounds']} rounds: median **{metrics['median_ms']:.2f} ms**, maximum **{metrics['max_ms']:.2f} ms**.", "",
             "These timings depend on the selected interpreter and host. Query-count and aggregation contracts are enforced by [performance tests](../../tests/test_performance_contracts.py)."]
    if browser:
        performance = browser["performance"]
        rows += ["", "## Browser picker", "", "Passing browser viewports: " + ", ".join(browser["viewports"]) + ".", "",
                 f"Synthetic history: {performance['rows']} readings; {performance['taps']} tile taps. Direct handler median **{performance['directMedianMs']:.2f} ms**, maximum **{performance['directMaxMs']:.2f} ms**. Frame median **{performance['frameMedianMs']:.2f} ms**. Chart repaints: **{performance['chartRepaints']}**; history changes: **{performance['historyChanges']}**.", "",
                 "The browser harness also verifies cold startup, drafts/races, save clearing, offline shell/queue, rotation/fullscreen, and agenda time. Timings measure synthetic tile handling, not network latency or performance on a physical iPhone."]
    rows += ["", "## Reproduce", "", "```sh", "python scripts/dev.py check", "python scripts/dev.py test --python /path/to/python",
             "python scripts/dev.py mobile", "python scripts/dev.py browser > /tmp/jobtracker-browser-result.log",
             "python scripts/measure_refactor.py --browser-report /tmp/jobtracker-browser-result.log", "```", "",
             "Browser reproduction requires Playwright as described in the [testing runbook](../runbooks/testing.md). A fresh measurement intentionally updates this dated snapshot; generated reference maps have separate deterministic drift checks."]
    return "\n".join(rows) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--browser-report", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/generated/refactor-report.md")
    args = parser.parse_args(argv)
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    try:
        content = report(args.baseline, math_benchmark(args.rounds), browser_result(args.browser_report) if args.browser_report else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Wrote refactor measurement snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
