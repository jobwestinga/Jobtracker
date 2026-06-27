# CLAUDE.md — agent & developer rules for JobTracker

Guidance for future Claude Code sessions working in this repository. Read this
before making changes. User-facing docs live in `README.md`; engineering rules
live here.

## What this app is

- A **local-only, macOS desktop time tracker**. PySide6 (Qt 6 Widgets) + SQLite.
- **Subjects** are the timed entities — you start/stop a timer on a subject and
  it records sessions. **Tasks/goals are a separate area** (a deadline to-do
  list) and are intentionally **not** connected to timed subjects yet.
- Personal-use, single-user, single-machine. Keep it **simple and personal**.
- Architecture layers: `core` (config, models, database, themes, timeutils,
  logging) → `services` (TrackerService) → `ui` (PySide6 widgets).

## Hard rules (do not break)

- **Never commit.** The user commits manually. Do not run `git commit`, `git
  push`, or open PRs unless explicitly told to.
- **No network behavior of any kind.** No telemetry, no cloud sync, no accounts,
  no update checker, no remote logging, no external APIs. The app must work
  fully offline and keep all data on the user's machine.
- **No cross-platform work.** macOS only. Don't add Windows/Linux packaging.
- **Don't delete an unfinished (active) session automatically.** Active-session
  recovery must keep resuming the primary open session, never silently drop it.
- **Overlapping sessions are allowed on purpose.** Do not add overlap rejection.
- Suspiciously long sessions are fine — do not block or auto-truncate them.
- The app must always still launch with `python main.py`, load existing data,
  and keep working: subjects, sessions, manual editing, archive/delete, graphs,
  themes, animated backgrounds.

## Time handling

- All datetimes are **naive local ISO-8601 strings** (`datetime.now().isoformat()`).
  There is **no timezone/UTC handling** and durations are wall-clock differences.
  This is accepted for now. A DST-spanning session can be off by ±1h.
- **All date/duration/day-boundary math goes through `jobtracker/core/timeutils.py`.**
  Do not re-implement date math in services or UI. If you need new time logic,
  add it there (it is the seam a future UTC migration will pass through).
- **Logical day** starts at **03:00 by default** (`day_start_time` setting),
  because the user's day ends when they sleep. Times before the boundary belong
  to the previous calendar date. The bar-chart aggregation attributes a whole
  session to the logical day of its **start** time. `split_by_logical_day()`
  exists (and is tested) for precise per-day splitting when a future heatmap /
  export needs it.
- The agenda timeline intentionally uses **calendar** days (it paints clock
  positions), not logical days.

## Testing

- Tests are **pytest**, in `tests/`. Run them with `python -m pytest`
  (install dev deps with `pip install -r requirements-dev.txt`).
- **Tests must never touch real user data.** `tests/conftest.py` sets
  `JOBTRACKER_DB_PATH` to a throwaway temp file *before* importing the package
  (which redirects the module-level `Database()` singleton), and each test gets
  its own isolated `Database(tmp_path)` via the `database`/`service` fixtures.
- Tests run **headless** — no QApplication / GUI. Keep core logic testable in
  the `core`/`services` layers, not buried in widgets.
- **Do not change core time-tracking behavior without tests.** If you touch
  start/stop, the 30s rule, recovery, day-bucketing, or import/export, add or
  update tests and run `python -m pytest` before finishing.

## Database & migrations

- Single SQLite file. Dev: `./data/jobtracker.db`. Frozen app:
  `~/Library/Application Support/JobTracker/jobtracker.db`.
- Schema migrations are additive, idempotent `ALTER TABLE` blocks in
  `Database._init_db()` guarded by `_column_exists`. Keep that pattern.
- **Before any non-additive / hard-to-reverse schema change** (e.g. renaming the
  legacy `tasks` table — which actually stores *subjects* — to `subjects`):
  - Explain the migration in the PR/description and in code comments.
  - Make it reversible/safe (back up or keep old columns) and test it on a copy.
  - Do **not** do the `tasks` → `subjects` rename casually; it touches every
    query and the import legacy-key handling.
- `last_active_at` (sessions) is a heartbeat timestamp updated ~once/minute while
  active. One UPDATE, no history rows. Keep it cheap.

## Logging

- Use `logging.getLogger("jobtracker")`. `setup_logging()` (called from
  `main.py`) writes to a rotating file under the data dir + stderr. **No remote
  logging.** Prefer logging over silent `except Exception: pass`.

## Style

- Concise, direct solutions over abstractions. Match surrounding code.
- Don't rewrite the whole app or do large UI rewrites for cleanup. Prefer small,
  tested, reversible changes. Run the relevant tests after changes.
