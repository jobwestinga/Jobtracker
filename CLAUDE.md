# CLAUDE.md — agent & developer rules for JobTracker

Guidance for future Claude Code sessions working in this repository. Read this
before making changes. User-facing docs live in `README.md`; engineering rules
live here.

## What this app is

- A **self-hosted, personal time tracker**. A macOS desktop app (PySide6 /
  Qt 6 Widgets + SQLite) and — as of the multi-device work — an iPhone web client
  talking to **the user's own server**. Single user, two devices, no third parties.
- **Subjects** are the timed entities — you start/stop a timer on a subject and
  it records sessions. **Goals are a separate, outcome-focused area** with
  descriptions and milestones. They are intentionally **not** connected to
  timed subjects.
- Personal-use and single-user. Keep it **simple and personal**.
- Architecture layers: `core` (config, models, database, themes, timeutils,
  sync_policy, logging) → `services` (TrackerService) → `ui` (PySide6 widgets).
  `core` and `services` contain **no Qt imports** and must stay that way: the
  server imports and runs the same `TrackerService` the desktop app runs, which
  is what keeps the two from ever disagreeing about a rule.

## The server (deployed)

Lives in `server/`, runs on the user's own Ubuntu 24.04 droplet, and imports
`jobtracker.core` + `jobtracker.services` directly — **the same `TrackerService`
the desktop runs**, which is why a rule like milestone-gated completion cannot
drift between clients.

- **This repository is public.** The server's hostname, its login, and the
  tailnet address live in `deploy/server.env`, which is gitignored. Read that
  file for the real values; never paste them into a tracked file, and never put
  an API token in the repo at all.
- Base URL: `https://$JT_TAILNET_HOST:$JT_HTTPS_PORT` — tailnet only, real Let's
  Encrypt certificate via `tailscale cert`. uvicorn binds `127.0.0.1:$JT_API_PORT`
  and `tailscale serve` publishes it. Nothing of JobTracker's is on a public port.
- **HTTPS is on 8443, not 443, because Caddy (in Docker) already binds
  `0.0.0.0:443`** for the user's websites and therefore intercepts tailnet
  traffic on 443 and fails the handshake with no matching cert. Don't "fix" this
  by changing Caddy — it serves live sites.
- Tailscale Serve routes on the **Host header**, so a request to the bare IP gets
  a 404 — always use the tailnet name. The server runs with `--accept-dns=false`
  so Tailscale never touches DNS for the user's other sites; the side effect is
  that MagicDNS does not resolve *on the server itself*, so test from there with
  `curl --resolve "$JT_TAILNET_HOST:$JT_HTTPS_PORT:$JT_TAILNET_IP"`.
- Deploy with `./deploy/install.sh` (renders the unit file from the template and
  restarts the service). Do not commit a rendered unit file.
- Layout under `$JT_ROOT`: code `app/`, venv `venv/`, database
  `data/jobtracker.db`, hashed tokens `secrets/tokens.json` (0600), nightly
  backups `backups/` (cron 04:17, 30 days, SQLite online-backup API).
- Endpoints: `/health` (no auth), `/ops` (the only write path), `/sync/pull`,
  `/sync/integrity`, `/api/snapshot`, `/api/active`, `/api/graphs/*`,
  `/api/sessions/day/{day}`.

Rules for the server:

- **`/ops` is the only way to write.** Every operation is named after the
  `TrackerService` method it runs, takes uids, and carries a client-generated
  `op_id`. The result is stored against that id and replayed on a repeat, so a
  retry after a dropped connection cannot double-insert. Never add a write path
  that bypasses `apply_op`.
- **A batch stops at the first failure** and reports `failed_index`. Do not make
  it skip and continue: an ordered outbox that lets later writes jump a failed
  one is exactly how a queue corrupts state.
- **Recovery runs once at startup**, never per request (it closes sessions).
  Request handlers that need the live timer call `_adopt_open_session()`.
- The change feed is produced by SQLite triggers in `server/feed.py`, keyed on
  **rowid, not uid** — a row's uid is filled by an `AFTER INSERT` trigger, so at
  INSERT-trigger time `NEW.uid` is still NULL. Rowids are safe to key on because
  every synced table is `INTEGER PRIMARY KEY AUTOINCREMENT` (never reused). The
  uid is resolved when the feed is read.
- Deletes archive the whole row into `deleted_rows` before it goes.

## Where the multi-device work stands

Approved plan: the server holds the one authoritative database; the Mac app and
the iPhone are equal clients of it. There is deliberately **no two-way merge
algorithm** — that whole class of sync bug is designed out rather than tested for.
The desktop keeps a local SQLite **mirror** that is a disposable read cache, so
the app still opens and shows history with the server unreachable, and writes made
offline wait in an ordered outbox.

**Done:** phase 1 — cross-machine row identity (`uid`), device ownership of
running sessions, device-scoped crash recovery. Phase 2 — the server above, live
and **seeded from the Mac's migrated database**, so both sides hold byte-identical
uids for all 1,830 rows (verified by hashing the sorted uid list per table on
each side). Any future seed must preserve that property or the first sync
duplicates everything. **Not built yet:** the desktop sync client (`jobtracker/sync/`, the
mirror and outbox) and the phone PWA. **The desktop app still talks to no network
at all** — it reads and writes its own local SQLite exactly as before.

## Hard rules (do not break)

- **Never commit.** The user commits manually. Do not run `git commit`, `git
  push`, or open PRs unless explicitly told to.
- **The only remote the app may ever talk to is the user's own server.** No
  telemetry, no analytics, no accounts with anyone else, no update checker, no
  remote logging, no third-party APIs, no cloud provider. All data stays on
  machines the user controls.
- **The desktop app must keep working with the server unreachable.** It opens,
  shows all history from its local mirror, and accepts changes into the outbox.
  Sync is also switchable off entirely, which returns the app to pure-local
  behaviour. Never make a UI path block on a network call.
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
- **Pick the interpreter deliberately.** The checked-out `venv/` has PySide6 but
  **no pytest**, and `python3` on PATH resolves to that venv, so a bare
  `python3 -m pytest` fails with `No module named pytest`. The interpreter that
  has both pytest and PySide6 is the framework build:
  `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest`
  (it is also what the "fast path" rebuild in *Definition of done* uses). Suite
  is ~278 tests and runs in a couple of seconds.
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
- Tables: `tasks` (subjects), `sessions`, `todo_tasks` (goals), `settings`,
  `milestones` (FK to `todo_tasks`), `goal_templates`. The
  `tasks`/`todo_tasks`/`TodoTask` names are legacy and stay for compatibility;
  user-facing terminology is **Subjects** and **Goals**. The **API uses the clean
  names** (`subjects`, `goals`) and maps to the legacy tables — that is how we get
  good nomenclature without the risky table rename.
- Additive columns currently added by `_init_db()`: `tasks.sort_order`,
  `tasks.is_archived`, `sessions.last_active_at`, `todo_tasks.template_id`,
  `todo_tasks.is_focused` (weekly focus, 0/1),
  `goal_templates.recurrence_day` (weekday 1–7 for weekly, day-of-month for
  monthly), `uid` on all five synced tables, and `sessions.device_id`. All
  guarded by `_column_exists`.
- **A column added to a synced table needs a matching field on its dataclass.**
  Rows are read as `Model(**dict(row))` over `SELECT *`
  ([database.py](jobtracker/core/database.py)), so a new column with no field on
  the model raises `TypeError` on *every* read. Add it to
  [models.py](jobtracker/core/models.py) in the same change.
- **Downgrade hazard:** once a database has been opened by a build that has the
  `uid` columns, an older JobTracker build cannot read it (same `SELECT *` reason).
  Pre-migration backups live in `~/JobTracker-backups/`.
- The **only** non-additive migration is the `sessions.note` DROP (needs SQLite
  ≥ 3.35; older versions leave the column in place and it is simply unused).
  Don't add a second one casually.

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
- `core/sync_policy.py` — what is shared between machines and how rows are
  identified (`SYNCED_TABLES`, `SYNCED_SETTING_KEYS`, `new_uid()`, the uid
  triggers). See "Row identity across machines" below.

The UI shell is `ui/app.py` (`MainWindow`) plus three mixins —
`SubjectsMixin`, `GoalsMixin`, `GraphsMixin`. `MainWindow` owns the single
`TrackerService` as `self.service`; **widgets and mixins never touch `Database`
directly**, they go through the injected service (dialogs take it as a
constructor argument). Page order in the stack is **0 = Goals, 1 = Subjects,
2 = Graphs** — several shortcut handlers test the index literally.

`ui/widgets/dialog_utils.py` and `ui/widgets/reorderable_list.py` are shared UI
infrastructure; see the two sections below before adding dialogs or list cards.

Goal editing lives in `ui/widgets/goal_dialog.py` (`GoalDialog`). An unused
`todo_task_dialog.py` used to sit beside it and was deleted — don't recreate it.

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

## Row identity across machines, and who owns a running timer

`core/sync_policy.py` is the single source of truth for what is shared and how a
row is identified. Both the desktop and (later) the server import it.

- **Integer row ids are private to one database and never cross the network.**
  Each database keeps its own autoincrement ids for foreign keys, Qt item data,
  and every existing query — which is why none of that code had to change. The
  `uid` (UUID4) is the only identity that travels.
- A uid is minted by an **`AFTER INSERT` trigger**, one per synced table, rather
  than by the ~40 write methods. No write path can forget, including
  `import_data()` and anything added later. The trigger fires only
  `WHEN NEW.uid IS NULL`, so an explicit uid — from a restored backup — is kept
  instead of being forked.
- Backups carry uids, so restoring one preserves cross-machine identity. A uid
  that would collide is dropped and re-minted (`_importable_uid`) rather than
  failing the restore.
- `SYNCED_SETTING_KEYS` is an allowlist: only `day_start_time` describes the data.
  Theme, graph range/hours, and `todo_order_mode` are per-machine and must not
  sync — the phone does not get to dictate the Mac's window state. `device_id` is
  in `DEVICE_LOCAL_SETTING_KEYS` and is explicitly skipped by `import_data()`:
  two installs sharing one device id would both claim the same running timer.

**Crash recovery is device-scoped, and this is load-bearing.**
`TrackerService.recover_open_sessions()` closes stale open sessions — so it may
run **once per process** (app launch, server start), never per request. Construct
with `recover=False` for a service that is only answering a query; that path
adopts the open session without closing anything. Recovery only ever touches
sessions whose `device_id` is this install's **or NULL** (historical rows, imported
backups). A session stamped with another device's id is left completely alone —
that is what stops the Mac from ending a timer the phone is running, and it is the
multi-device form of the existing "never drop an unfinished session" rule.

## The desktop sync client (`jobtracker/sync/`)

- `state.py` — the local `sync_outbox` and `sync_state` tables. Not synced: they
  describe this machine's relationship with the server, not the user's data.
- `client.py` — HTTP over **stdlib `urllib`** on purpose. The app's only
  third-party dependency is PySide6; keeping it that way means nothing new has
  to be bundled or signed into the .app.
- `engine.py` — push, pull, verify, repair. No Qt, so it is fully testable
  headlessly against an in-process server (`tests/test_sync_engine.py`).
- `service.py` — `SyncedTrackerService`, the service the UI actually gets when
  sync is on. Every mutation is applied locally *and* queued as an operation.
- `qt_worker.py` — the app's only background thread. It opens its **own**
  database connection (sharing one sqlite3 connection across threads is not
  safe); `Database.__init__` enables WAL + `busy_timeout` so the UI thread keeps
  reading while it writes.
- `settings.py` — the on/off switch and server URL are device-local settings; the
  **token is kept in its own 0600 file, never in the settings table**, because
  `export_data()` copies that table into every backup the user exports.

**Redeploy the server whenever `server/ops.py` changes.** A client that emits an
operation an older server does not know gets a 404 mid-batch — which is safe (the
queue blocks and nothing is lost) but looks like a sync bug. This has already
happened once. `tests/test_sync_service.py` checks the client's ops exist on the
server *in the test process*; only a redeploy makes that true in production.

Order within one pass is load-bearing: **push, then pull, then verify.** Pushing
first stops a pull from overwriting an edit still sitting in the outbox.

- A refused operation **blocks the queue** and is reported; it is never skipped.
  Skipping would let "recreate" overtake "delete".
- A create op carries the uid the client already minted (`_adopt_uid` on the
  server honours it). Without that the server would invent a second identity and
  the client would pull its own row back as a duplicate.
- **`full_resync()` must never run while the outbox is non-empty** — it deletes
  every local row, which would discard unsent work. `sync()` enforces this.
- Only `SYNCED_SETTING_KEYS` are restored by a resync, so a phone can never
  overwrite this machine's theme or graph range.

**TLS trap, already hit once:** the python.org framework build — the one
PyInstaller freezes into the .app — does not read the macOS keychain. It looks in
its own empty `etc/openssl/`, so a perfectly valid Let's Encrypt certificate
fails with `CERTIFICATE_VERIFY_FAILED` while `curl` on the same machine succeeds.
`client.build_ssl_context()` loads `/etc/ssl/cert.pem` (part of macOS, present
for the frozen app too). Never "fix" a TLS error by disabling verification.

## Dialogs are inline, never native windows (macOS constraint)

**Never show a `QDialog` as its own window, and never reparent one into the
main window.** On macOS `QDialog.done()` can recreate the dialog's original
native `NSWindow` after it has been reparented; in native fullscreen macOS then
promotes that orphan into its own Space. Every editor in this app therefore
subclasses `InlineDialog` (a plain `QWidget` for its whole lifetime) from
`ui/widgets/dialog_utils.py`, which provides the `QDialog`-shaped API the code
expects (`accept/reject/done/result/finished/accepted/rejected`, plus
`Accepted`/`Rejected`).

- Show one with `open_dialog(dialog, on_finished)`; it hosts the widget in an
  `_InlineDialogLayer` over the main window and hands the result to the
  callback. Message boxes go through `information/warning/critical/question`
  from the same module — they build an inline `_InlineMessageDialog`, so they
  are **callback-based, not blocking**. Don't call `QMessageBox.exec()`.
- `dialog_owner(dialog)` returns the caller-supplied parent (the inline layer
  reparents), which is how nested dialogs reach `MainWindow` helpers such as
  `_register_undo`.
- The window property `_jt_inline_dialog_count` tracks open inline dialogs; the
  global shortcuts refuse to fire while it is non-zero.
- Same family of macOS bugs: `ReorderableCardList` hides shortcut badges during
  a drag, and `subjects_mixin._update_tracking_state_inplace()` mutates the
  existing cards on start/stop instead of rebuilding the list. Rebuilding the
  card list mid-interaction is what trips the fullscreen compositor. Keep both.

## Keyboard shortcuts and the one-step undo

Installed in `MainWindow._install_shortcuts()`, all `Qt.WindowShortcut`, all
gated on `_shortcut_focus_allows_navigation()` (no inline dialog, popup, modal,
or text-entry widget focused):

- `←` / `→` — move between the three pages.
- `1`–`9` — act on that row of the current page: open the goal (Goals page) or
  start/switch to that subject (Subjects page). Kept as **per-digit**
  `QShortcut`s so the switch-confirm prompt can disable the one conflicting key
  while it is open (two live shortcuts on one key are ambiguous to Qt and
  neither fires). Badges renumber with the list order.
- `Esc` — leave the Completed-goals or Archived-subjects view.
- `W` / `M` / `Y` / `A` — graph range presets (Graphs page only); they write
  `graph_range` and clear any custom range.
- `Ctrl+Z` / `⌘Z` — the undo.

Undo is **one in-memory step, not a stack**: `_register_undo(callback)` stores a
single reversible action and `_perform_undo()` consumes it and reloads.
Currently registered by archive/unarchive of a subject, subject and goal
reordering, and goal-dialog actions. Keep it deliberately small — don't grow it
into a general undo stack, and don't register anything destructive that can't be
reversed by the stored callback alone.

## Goals, milestones, and recurring generation

- A Goal is stored in the legacy `todo_tasks` table. It has a title
  (`name`), description (`notes`), completion state, order, an optional
  `template_id`, and the weekly-focus flag `is_focused`. Do not reconnect it to
  timed Subjects.
- **Weekly focus is a plain flag, not a mode.** `toggle_goal_focused()` flips
  `todo_tasks.is_focused`; the card shows ★/☆ (accent when focused) and the
  context menu offers "Focus This Week" / "Remove Weekly Focus" on active goals
  only. It **must never affect completion rules, ordering, or generation** — it
  is a visual marker that survives the JSON backup round-trip
  (`tests/test_focus.py`). No auto-clearing at week end.
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
- `goal_templates.recurrence_day` holds the schedule: ISO weekday 1–7 for
  weekly, day-of-month for monthly (clamped to the month's length), ignored for
  daily. `_template_is_due()` fires **on or after** that day (`>=`), so a
  template is never skipped just because the app wasn't opened that day.
- A template title may contain the `{date}` placeholder; it is replaced with the
  generated instance's logical date (`_template_instance_title`).
- The Goals page has an active list and a **Completed** view (toggle button /
  `Esc`). Goals are *completed*, never "archived" — archiving is a Subjects-only
  concept. Reopening happens from the goal dialog ("Reopen Goal") via
  `uncomplete_goal()`.
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
- Settings/graph dialogs persist via the `db` settings table. Current graph
  keys: `graph_range` (`weeks|months|year|all|custom`), `graph_view_mode`
  (`bar|agenda|heatmap`), `graph_custom_start` / `graph_custom_end` (only when
  range == "custom"), `graph_hour_start` / `graph_hour_end` (agenda window,
  defaults 6 / 23), `graph_fit_horizontal` (default "1"), `graph_autofit_hours`
  (default "0"). Other keys: `theme_fx`, `theme_palette`, `day_start_time`,
  `todo_order_mode` (`manual|deadline`), `device_id`. Only `day_start_time` is
  shared between machines — see `core/sync_policy.py`.
- **`graph_grouping` is obsolete.** Grouping is derived from the selected range
  (`grouping_for_preset` / `grouping_for_span`), and `MainWindow.__init__`
  deletes the stale setting once at launch. Don't persist grouping again.

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
