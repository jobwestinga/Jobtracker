# Refactor measurement snapshot

Measured 2026-10-11 against baseline `607f5ddac2d46ccfc731d3805cd1784bb9a6d732`.

Counts measure the review surface of composition/facade files. Implementations moved into feature owners; compatibility wrappers and focused tests add code elsewhere.

| File | Baseline lines | Current lines |
| --- | ---: | ---: |
| `web/app.js` | 1703 | 270 |
| `jobtracker/core/database.py` | 1472 | 400 |
| `jobtracker/services/tracker_service.py` | 1148 | 505 |
| `jobtracker/ui/app.py` | 803 | 492 |
| `server/app.py` | 543 | 119 |
| `server/ops.py` | 697 | 112 |
| `jobtracker/sync/engine.py` | 440 | 318 |
| `tests/test_ui_smoke.py` | 2522 | Split into tests/ui groups |

## Synthetic weight mathematics

Python point selection plus LOESS, 1060 daily readings, 5 rounds: median **15.22 ms**, maximum **19.99 ms**.

These timings depend on the selected interpreter and host. Query-count and aggregation contracts are enforced by [performance tests](../../tests/test_performance_contracts.py).

## Browser picker

Passing browser viewports: 390x844, 844x390, 1280x900, 320x740.

Synthetic history: 1060 readings; 40 tile taps. Direct handler median **0.30 ms**, maximum **0.50 ms**. Frame median **16.60 ms**. Chart repaints: **0**; history changes: **0**.

The browser harness also verifies cold startup, drafts/races, save clearing, offline shell/queue, rotation/fullscreen, and agenda time. Timings measure synthetic tile handling, not network latency or performance on a physical iPhone.

## Reproduce

```sh
python scripts/dev.py check
python scripts/dev.py test --python /path/to/python
python scripts/dev.py mobile
python scripts/dev.py browser > /tmp/jobtracker-browser-result.log
python scripts/measure_refactor.py --browser-report /tmp/jobtracker-browser-result.log
```

Browser reproduction requires Playwright as described in the [testing runbook](../runbooks/testing.md). A fresh measurement intentionally updates this dated snapshot; generated reference maps have separate deterministic drift checks.
