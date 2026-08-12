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
  `agenda_hour()` (after-midnight work maps to 24..27), `split_by_logical_day()`,
  `clock_time_in_logical_day()` (inverse of `logical_day`) and
  `shift_session_to_logical_day()` (used by session duplication).
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

`TrackerService.duplicate_session(session_id, to="today"|"next_day")` copies a
closed session onto another logical day keeping clock time, duration, and
subject. `shift_session(session_id, seconds)` nudges a session in time (both
ends move, duration preserved) — that is what the ±15m / ±1h buttons use.
Editing a session can also move it to another subject (subject dropdown in
`SessionDialog`); that is one UPDATE on the same row, so the session id and all
times survive.

**Sessions may be created in the future on purpose** (planning ahead). Nothing
blocks a future start, so graph windows must reach them: `graph_end_day()`
returns `max(today, logical day of the latest session)`. Don't reintroduce a
"no future sessions" guard.

**Anchor windows on the real today; only stretch the END to `graph_end_day()`.**
`_resolve_logical_window()` and `graphs_mixin._window_and_grouping()` both
compute their start from `logical_day(now)` and then extend the end. Anchoring
the whole window on `graph_end_day()` slides it into the future and silently
drops real past days — e.g. one session 7 days ahead pushed the "Weeks" preset
a whole week forward, hiding the week you were looking at. Regression test:
`test_future_session_extends_window_without_dropping_past_days`.

**Sessions have no note field.** `sessions.note` was dropped (the one
non-additive migration in the schema, see `_init_db`) because it was never used.
Subject notes (`tasks.notes`) and milestone notes stay. Don't add a session note
back without a real migration.

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
- Active daily/weekly/monthly templates are checked on startup and when the
  Goals tab is opened. `last_generated` stores the logical period key. A due
  template inserts one ordinary editable goal at the top. Generation must stay
  idempotent within a period.
- **Generation is purely ADDITIVE.** Prior unfinished instances stay in the
  active list on purpose — the user wants a visible backlog of what still needs
  doing. Do not add auto-expiry, "missed" states, or any automatic removal; an
  earlier attempt at that was explicitly rejected.
- Weekly templates fire on or after their scheduled weekday (`>=`), so a
  template is never skipped just because the app wasn't opened that day.
- The authoritative JSON backup includes goals, milestones, templates, their ID
  relationships, and settings. Restore must preserve repeated generated goals
  that legitimately share a title.

## Heatmap

- The heatmap is the third Graphs view and uses tracked session time only—never
  goal completion, streaks, or insights.
- `TrackerService.get_heatmap_data()` uses the same logical-day/start-attribution
  rule as the bar chart and includes a live session. Empty days are zero-filled.
- Clicking a cell opens `DaySessionsDialog`, which uses
  `get_sessions_for_logical_day()`. It edits / duplicates / deletes the selected
  session itself (via `apply_session_edits()` and `duplicate_session()`), and
  "Open subject history…" hands the same session id to `ManageSessionsDialog`
  so it is preselected there.

## Clicking a session must always target THAT session

Graph views carry session identity, never just a day:

- The agenda keeps `session_id` on every painted block. **Either mouse button
  behaves the same**: a click on a block edits that exact session
  (`AgendaViewWidget.session_clicked`), a click on empty column space opens the
  day (`day_clicked`). A block with no id (the live session) falls back to the
  day.
- Every session list stores the whole session dict per row and accepts
  `select_session_id` so the right row is preselected. Never reopen a list and
  rely on "row 0" — that was a real bug: editing from the agenda used to land
  on the subject's newest session.

## One session list, one set of session actions

`ui/widgets/session_list.py` is the single implementation shared by
`ManageSessionsDialog` (per subject) and `DaySessionsDialog` (per logical day):
`SessionListView` (rows, colour dot, selection styling, Enter/double-click),
`build_move_row()`, and the `edit_session` / `duplicate_session_to_today` /
`delete_session` / `shift_session` actions with their confirm copy.

- Rows are plain dicts (`session_id, subject_id, subject_name, color,
  start_time, end_time, duration_seconds`); `session_row()` adapts a `Session`
  model. `session_id` is None only for the live session, which is shown but
  never editable (`require_editable`).
- Reaching "the sessions menu" from a subject card or from a graph must land on
  this code, not a lookalike. If you add an action, add it here. `graphs_mixin`
  routes agenda clicks through `edit_session()` for exactly this reason — when
  it had its own copy, the agenda silently missed delete.
- `SessionDialog` reports `DELETE_RESULT` (like `GoalDialog`) instead of
  deleting itself; `edit_session()` performs the removal. Every entry point
  (agenda block, per-day list, per-subject list) therefore deletes identically.
- `session_dialog` imports `build_move_row` from this module, so `session_list`
  imports `SessionDialog` lazily inside `edit_session()` — keep it that way or
  the import graph becomes cyclic.
- Theme tokens come from `resolve_tokens()`, which walks up to the window;
  reading `parent._tokens` fails when a dialog opens another dialog.

Rules when extending:

- Weeks are Monday-start. Don't change that convention.
- The bar chart attributes a whole session to the logical day of its START. The
  agenda clamps sessions that spill past the logical-day end. Keep both.
- Animations pause when the app is unfocused/minimized (`FxBackgroundWidget.
  set_animating`, driven by `applicationStateChanged` / `changeEvent`). Don't
  reintroduce always-on full-window repaints.
- Settings/graph dialogs persist via the `db` settings table; new graph keys:
  `graph_grouping`, `graph_custom_start`, `graph_custom_end` (range == "custom").

## Colours in stylesheets

**Never write `#RRGGBBAA` (8-digit hex) in a Qt stylesheet.** Qt parses it as
`#AARRGGBB`, so `"{ACCENT}44"` on `#3B82F6` renders **green** (`rgb(130,246,68)`
at alpha 59), not a faded blue. This silently mis-coloured the session-list
selection and the Glow/Clean card borders.

Use `rgba(r, g, b, a)` with an integer alpha 0–255 — Qt honours it (`68` renders
identically to `0.27`). `themes.py` precomputes `<TOKEN>_A<alpha>` variants for
this; add the alpha you need to the tuple in `get_tokens()` rather than
concatenating hex digits.

## Style

- Concise, direct solutions over abstractions. Match surrounding code.
- Don't rewrite the whole app or do large UI rewrites for cleanup. Prefer small,
  tested, reversible changes. Run the relevant tests after changes.

## Layout and visual conventions

Tried and **rejected** by the user — don't reintroduce these:

- A capped/centred content column. Content is deliberately **full-bleed**: the
  screen should look filled at fullscreen width.
- A dark contrast stroke around the bar-chart totals. The numerals keep only
  their coloured glow (`_intensity_style` tiers) over `TEXT_PRIMARY`.
- Any special colouring for today's agenda column. Every column is styled
  identically; the current-time marker line is the only "now" cue.

Current conventions:

- Card and chart surfaces are **near-opaque** (`*_A248`, panels at alpha 238) so
  the animated wash never shows through content. Don't lower these back.
- Subject/goal rows carry colour only in the dot and the left border; body text
  stays neutral. Chart fills keep full colour.
- Row heights are a deliberate middle ground: subject cards 68px, goal cards
  72px. Neither the old airy 78px+hint-text nor the 58px crush.
- Daily axis labels read `Mon 03` (weekday + day). No gridlines on the bar chart
  by choice — the two edge labels are the whole axis.
- Agenda blocks are a neutral base + ~35% subject tint + a 3px saturated left
  bar, with labels elided and clipped to their block.
- The goal ✓ is a quiet ring (`TEXT_DIMMED`) that turns `ACCENT_GREEN` on hover;
  a permanently saturated green circle fought with the card palette.
- A card's description must never wrap: `_ElidedLabel` truncates it and needs an
  **Expanding** size policy, otherwise Qt spreads the row's slack evenly and
  pushes the badge and ✓ into mid-card.
