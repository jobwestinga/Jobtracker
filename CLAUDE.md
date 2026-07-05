# CLAUDE.md — agent & developer rules for JobTracker

Guidance for future Claude Code sessions working in this repository. Read this
before making changes. User-facing docs live in `README.md`; engineering rules
live here.

## What this app is

- A **local-only, macOS desktop time tracker**. PySide6 (Qt 6 Widgets) + SQLite.
- **Subjects** are the timed entities — you start/stop a timer on a subject and
  it records sessions. **Goals are a separate, outcome-focused area** with
  descriptions and milestones. They are intentionally **not** connected to
  timed subjects.
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

## Definition of done — ALWAYS test + rebuild

After ANY code change, before reporting back to the user:

1. **Run the full test suite**: `python3 -m pytest`. Everything must pass —
   not just the tests near the change.
2. **Rebuild and reinstall the app** so the installed app matches the source.
   Fast path (system Python already has PyInstaller + PySide6, no venv needed):

   ```bash
   rm -rf build dist
   pyinstaller JobTracker.spec --noconfirm
   rm -rf /Applications/JobTracker.app
   cp -R dist/JobTracker.app /Applications/
   xattr -cr /Applications/JobTracker.app
   codesign --force --deep -s - /Applications/JobTracker.app
   codesign -v /Applications/JobTracker.app
   ```

   Sign AFTER copying to /Applications: the repo lives in iCloud Drive, which
   re-attaches extended attributes between `xattr -cr` and `codesign`, breaking
   signatures made inside the repo's `dist/`.

   (`./build_macos.sh` does the same through a venv; slower but equivalent.)
3. Before replacing `/Applications/JobTracker.app`, check whether the app is
   running and whether a session is being tracked (`pgrep -lf JobTracker.app`,
   open sessions in the prod db). Never yank the bundle mid-session; if it is
   running idle, the old process keeps working but tell the user to relaunch.
4. Back up both databases (`data/jobtracker.db` and the Application Support
   one) before any schema migration, even additive ones.

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
- Prompt 3 adds two tables and one nullable column, all additive:
  `milestones` (FK to legacy `todo_tasks`), `goal_templates`, and
  `todo_tasks.template_id`. The `todo_tasks`/`TodoTask` names remain for
  compatibility; user-facing terminology is **Goals**.

## Logging

- Use `logging.getLogger("jobtracker")`. `setup_logging()` (called from
  `main.py`) writes to a rotating file under the data dir + stderr. **No remote
  logging.** Prefer logging over silent `except Exception: pass`.

## Feature module map (added after the foundation phase)

Pure, UI-free, fully unit-tested core logic — extend these rather than putting
logic in widgets:

- `core/timeutils.py` — parsing, durations, logical day, week/month bucket keys,
  `agenda_hour()` (after-midnight work maps to 24..27), `split_by_logical_day()`.
- `core/recovery.py` — crash-recovery decision: `build_recovery_info()` returns
  None for small gaps (don't prompt), else the numbers the dialog shows;
  `end_time_for_choice()` maps a choice to an end datetime. Gap threshold default
  5 min. The dialog (`ui/widgets/recovery_dialog.py`) is a thin shell.
- `core/colors.py` — `suggest_colors(existing_active_colors)` for the subject
  dialog. Only ACTIVE (non-archived) subject colours are passed in.
- `core/export_bundle.py` — CSV builders + `write_zip()`. JSON stays the only
  restore format; CSVs are read-only. Daily summary respects the logical day.
- `core/auto_backup.py` — rotating JSON safety copies written on every clean
  quit to `DATA_DIR/backups/` (`autobackup_*.json`, newest 10 kept). Only
  `autobackup_*` files are ever pruned; a backup failure must never block
  quitting.

Service methods doing the analytics (all logical-day aware, include the live
session): `get_subject_breakdown(grouping=daily|weekly|monthly, days/start/end)`,
`get_agenda_data()`, `get_subject_deletion_summary()`, `get_active_recovery_info()`,
`resolve_recovery()`, `get_heatmap_data()`, and
`get_sessions_for_logical_day()`. `get_daily_subject_breakdown()` is a thin
back-compat wrapper. Subject card totals come from `get_subject_stats_map()`
(one GROUP BY for all subjects + the live session, attributed by its START
time like the bar chart).

Switching subjects while tracking goes through `TrackerService.switch_subject()`
and, in the UI, always behind a confirm prompt: the running session keeps
ticking until the user confirms, and the old session gets the normal sub-30s
stop rule. Number-key shortcuts use the same confirm path — never a silent stop.

## Goals, milestones, and recurring generation

- A Goal is stored in the legacy `todo_tasks` table. It has a title
  (`name`), description (`notes`), completion state, order, and optional
  `template_id`. Do not reconnect it to timed Subjects.
- Completion is manual and milestone-gated in `TrackerService.complete_goal()`:
  all milestones must be checked, unless the goal has none. Adding or unchecking
  a milestone on a completed goal reopens it so the invariant stays true.
- Completed goals remain queryable and reopenable; their milestones are never
  hidden or deleted by completion.
- Active daily/weekly/monthly templates are checked on startup and approximately
  once per minute while the app is open. `last_generated` stores the logical
  period key. A due template inserts one ordinary editable goal at the top;
  prior unfinished instances remain. Generation must stay idempotent.
- The authoritative JSON backup includes goals, milestones, templates, their ID
  relationships, and settings. Restore must preserve repeated generated goals
  that legitimately share a title.

## Heatmap

- The heatmap is the third Graphs view and uses tracked session time only—never
  goal completion, streaks, or insights.
- `TrackerService.get_heatmap_data()` uses the same logical-day/start-attribution
  rule as the bar chart and includes a live session. Empty days are zero-filled.
- Clicking a cell opens `DaySessionsDialog`, which uses
  `get_sessions_for_logical_day()` and delegates edits to the existing subject
  session manager.

Rules when extending:
- Weeks are Monday-start. Don't change that convention.
- The bar chart attributes a whole session to the logical day of its START. The
  agenda clamps sessions that spill past the logical-day end. Keep both.
- Animations pause when the app is unfocused/minimized (`FxBackgroundWidget.
  set_animating`, driven by `applicationStateChanged` / `changeEvent`). Don't
  reintroduce always-on full-window repaints.
- Settings/graph dialogs persist via the `db` settings table; new graph keys:
  `graph_grouping`, `graph_custom_start`, `graph_custom_end` (range == "custom").

## Style

- Concise, direct solutions over abstractions. Match surrounding code.
- Don't rewrite the whole app or do large UI rewrites for cleanup. Prefer small,
  tested, reversible changes. Run the relevant tests after changes.
