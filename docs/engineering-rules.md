# Engineering rules

This is the canonical developer and agent guide. Read it before changing the
app. [The documentation index](index.md) links feature ownership, architecture,
contracts and operational runbooks. [README](../README.md) describes the product.

## Working autonomously

- Complete the authorized work and appropriate verification without asking the
  user to approve routine plans or equivalent implementation choices.
- Reuse authorization already given in the conversation. Assume reasonable
  optional details and report material assumptions in the result.
- Ask only when essential information, access or a required approval prevents
  progress. Continue independent work while a required answer is pending.
- Report completed changes, meaningful validation and remaining blockers
  concisely. Respect the scope; do not commit or publish unless authorized.

## What this app is

- A **self-hosted, personal time tracker**. A macOS desktop app (PySide6 /
  Qt 6 Widgets + SQLite) and an iPhone web client talking to **the user's own
  server**. Single user, no third parties.
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

## The own-server API

[server/](../server/__init__.py) imports the same pure core and services as the
Mac. Authoritative rules live in services, so milestone completion, timer rules
and weight identity cannot diverge between clients.

- This repository is public. Deployment hostnames, logins and private network
  addresses belong in the ignored `deploy/server.env`, never in tracked files.
  Never put API tokens, rendered service units or real user data in the repo.
- The configured HTTPS endpoint is private to the user's network. Uvicorn binds
  loopback; the configured Tailscale Serve endpoint publishes it. Preserve the
  existing HTTPS port and other hosted services. Do not change their proxy or
  DNS configuration to make JobTracker work.
- Tailscale Serve routes on the Host header: use the configured tailnet hostname,
  not a bare IP. Preserve the server's `--accept-dns=false` configuration. If
  server-local DNS cannot resolve the hostname, use the configured address with
  `curl --resolve` as described in the [deployment runbook](runbooks/deploy.md).
- Routine deployment uses [deploy/install.sh](../deploy/install.sh): an online
  database backup, source and web assets, existing-unit verification and a
  supervised restart. Provisioning system files is a separate `--provision`
  action requiring the configured sudo access; do not replace unrelated files.
- Deployment keeps code, its environment, data, token hashes and backups separate.
  Token files are private (0600). Scheduled SQLite backups use the online backup
  API, including committed WAL data.
- Read routes are owned by [server/routes](../server/routes/__init__.py).
  `/health` is unauthenticated; data routes require the shared device dependency.
  `/ops` is the only write path. The root static mount is registered **last**.
- [server/runtime.py](../server/runtime.py) owns the shared state and lock.
  Routers import that owner, never the app composition module. Compatibility
  aliases in `server.app` retain the same objects for dependency overrides and
  existing tests.

## The phone app (`web/`)

Plain HTML/CSS/ES modules — no framework, no bundler, no npm. Deploying it is
the same rsync that ships the server, and changing a colour needs nothing
installed. Served by the API itself; static files need no token, the data behind
them does.

- Authoritative rules stay in shared services. `/api/context` supplies the
  logical day/boundary; server queries supply graph totals. Client date helpers
  and optimistic projections support offline display and captured user input,
  not an alternate milestone gate, timer policy or aggregation rule. Reconcile
  provisional state with the canonical server answer.
- Writes go through the same idempotent operations, with a `localStorage` outbox
  when there is no signal. Anything already queued is sent first, so a delete can
  never overtake the create it refers to.
- Every action paints optimistically before the request goes out (a tap that
  waited a round trip felt broken), then the refresh that follows replaces the
  guess with the server's answer.
- Tab order: Goals, Subjects, Sessions, Graphs, Weight.
- A fresh phone launch starts on Weight; foreground resume preserves the current
  tab and draft. A durable weight save clears its picker unless a newer draft
  was entered while saving. Fullscreen plots keep their DOM and resize on rotation.
- **The app is a fixed frame**: `#app { position: fixed; inset: 0 }`, flex
  column, `#views` scrolling in the middle, tab bar as a flex item. Not
  `height: 100dvh` — in an iOS standalone app dvh resolves before the viewport
  settles, so the frame came up short and left a strip of page background below
  the tab bar, which then "fixed itself" on rotation. `inset: 0` is always the
  real viewport. Nothing in the layout may depend on page height.
- No `backdrop-filter` on the chrome. Nothing scrolls under the bars any more,
  so it was a GPU composite every frame for a blur of a solid colour — and the
  translucency is what made the bottom look two-toned.
- **Bars are summed per SUBJECT before drawing.** The API returns one segment per
  *session*. Summing first avoids a minimum block height inflating long buckets
  until every bar reaches the ceiling.
- Poll while foregrounded, never while a sheet is open. Preserve drafts and
  existing interactive DOM during refreshes; timer-only updates must not rebuild
  a form or chart.
- The minute poll asks `/api/context`. Changed feed heads update the mirror;
  unchanged data only updates elapsed time. Use an initial/repair snapshot when
  needed, and load weight lazily through `/api/weight`. Keep response compression
  enabled rather than increasing repeated full-history requests.
- `sw.js` makes it open with no signal. **Network-first everywhere**, falling
  back to the cache: the app is deployed by rsync and reloaded by hand, so a
  cache-first shell would keep serving yesterday's JavaScript after a deploy.
  Bump `VERSION` in `sw.js` when the shell changes, and keep `SHELL` complete
  for every transitive local import and stylesheet. The worker caches shell assets
  only, never API responses, and never replays writes. The app's own outbox owns
  retry identity and order; keep replay in one place.

## Server write and replay contracts

- **`/ops` is the only way to write.** Every operation is named after the
  `TrackerService` method it runs, takes uids, and carries a client-generated
  `op_id`. The result is stored against that id and replayed on a repeat, so a
  retry after a dropped connection cannot double-insert. Never add a write path
  that bypasses `apply_op`.
- **A batch stops at the first failure** and reports `failed_index`. Do not make
  it skip and continue: an ordered outbox that lets later writes jump a failed
  one is exactly how a queue corrupts state.
- **Automatic startup recovery is device-scoped and disabled on the server.**
  Server services use `recover=False`; reads adopt the newest persisted open row
  with `_adopt_open_session(include_other_devices=True)`. An explicitly requested
  `resolve_recovery` operation remains available through `/ops` and targets the
  captured session UID.
- Timer operations carry captured local timestamps and stable session uids.
  A queued stop, heartbeat, switch, or recovery must target its own session and
  never a newer timer. Timed offline replays may preserve overlapping open rows;
  stop only closes the addressed row.
- `Database.atomic()` commits mutations and their outbox/replay records together.
  Keep method-level commits inside that context deferred. A receipt is bound to
  operation parameters and authenticated principal, so `op_id` reuse for a
  different request is rejected rather than replayed.
- The change feed is produced by SQLite triggers in `server/feed.py`, keyed on
  **rowid, not uid** — a row's uid is filled by an `AFTER INSERT` trigger, so at
  INSERT-trigger time `NEW.uid` is still NULL. Rowids are safe to key on because
  every synced table is `INTEGER PRIMARY KEY AUTOINCREMENT` (never reused). The
  uid is resolved when the feed is read.
- Deletes archive the whole row into `deleted_rows` before it goes.
- **A missing target is a recorded skip, not an error.** `_target_uid()` raises
  `Skipped` for edits and deletes whose row is gone; `_require_uid()` still
  raises 404, but only for *creates*, where dropping the operation would lose
  data. State refusals (a second timer, a gated goal) come back in the payload
  rather than as 4xx. This is not politeness: the outbox stops at the first
  refusal, so anything that can never succeed would jam every later write from
  that device forever — delete a session on the phone offline, delete it on the
  Mac, reconnect, and that used to be a permanent jam.
- Use `sync_policy.WireCodec` when converting multiple rows. It loads each
  referenced table once, rather than doing a foreign-key lookup per row, and
  falls back to a direct lookup for rows created after it was built.
- [server/ops.py](../server/ops.py) retains the public registry and `apply_op`
  receipt/transaction boundary. Domain handlers live under
  [server/operations](../server/operations/__init__.py), with explicit registration.
  Preserve parameter/principal validation, atomic mutation plus receipt and
  replay ordering centrally. Do not add handler discovery side effects or move
  receipt ownership into domain handlers. See the
  [operation map](generated/operations.md) and [write path](architecture/write-path.md).

## Multi-device model

The server holds the authoritative database. Mac and phone are equal clients;
there is no two-way merge algorithm. The desktop keeps a local SQLite mirror
for offline reads, and offline writes wait in its ordered outbox. The mirror is
replaceable **only when there is no unsent work**.

- Initial server seeds must preserve existing UIDs; validate identity using sorted
  UID hashes before enabling sync. Re-minting the same history duplicates it.
- Desktop sync is off by default. Disabled sync retains pure-local behavior.
- The phone supports timer/session, subject, goal/template, graph and weight
  workflows using the same server operations.
- `weight_measurements` is `weights` on the wire. `weight_uid_aliases` preserves
  daily manual-save identities after offline coalescing; it is bookkeeping, not
  a separately replicated table. Phone snapshots omit weight rows, which load
  through `/api/weight`. All weight writes go through `/ops`.
- Synced backup restores queue `import_backup` with normalized post-merge data,
  stable UIDs and shared settings only. The server preserves known canonical
  UIDs and maps legacy integer parent IDs for new children. Restore, outbox and
  retry receipts stay atomic; full backups preserve weight aliases.

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
  exists (and is tested) for precise per-day splitting when an export needs it.
  It does not change the existing bar-chart attribution rule.
- The agenda uses **logical days**, matching `get_agenda_data()` and the native
  view. Post-midnight hours appear as 24+ in the prior logical day's column.
  Weight entries separately use calendar dates. The phone's current-time line
  uses the agenda API's `day_start_hour` and updates on minute ticks/resume.

## Definition of done

- Run focused checks while implementing, then the complete suite before delivery
  of code changes. Run boundary, reference and shell-asset checks as well. Use the
  [testing runbook](runbooks/testing.md) and report any check that cannot run.
- Changes to time tracking, recovery, day bucketing, sync, import/export or other
  durable data paths need meaningful regression coverage. Exercise failure and
  retry behavior as well as ordinary success.
- Package desktop code changes so the installed application can match the source.
  Use the [build and install runbook](runbooks/build-install.md): stage outside
  iCloud, verify the signed bundle and use the guarded installer. Never delete
  the installed bundle and copy a replacement over it manually.
- An open session on any device or any pending outbox blocks installation. Respect
  the installer queue; do not force-kill the app, discard queued writes or clear
  an active timer to make an update proceed. Previously authorized installation
  does not require another permission request.
- Before a migration, take SQLite online backups of every affected existing
  database. A code rollback must not overwrite live databases. Rehearse difficult
  migrations against copies; use the [incident runbook](runbooks/incidents.md).
- Deploy server or phone changes within the user's authorized scope through the
  [deployment runbook](runbooks/deploy.md), and verify the running version.
  Local tests alone do not prove a deployed server supports a new operation.
- Documentation-only changes need link/reference checks, not an app rebuild.

## Testing

- Tests use pytest under [tests/](../tests/conftest.py); native tests use offscreen
  Qt and isolated databases. Core/service tests remain independent of Qt.
- Choose an interpreter with the runtime, development and server test dependencies.
  Use the explicit `--python` option in `scripts/dev.py`; do not assume the shell's
  Python has pytest and PySide6. The [testing runbook](runbooks/testing.md) lists
  environment setup and the separate Node/browser checks.
- Tests must never touch real user data. `tests/conftest.py` redirects
  `JOBTRACKER_DB_PATH` before importing the package; each database/service fixture
  uses a temporary database. Do not import the eager compatibility `db` singleton
  in a tool before isolating its path.
- Reusable test fixtures belong in [tests/support](../tests/support/__init__.py),
  not collected test modules. Native tests are grouped under `tests/ui/`.
- Add focused tests for start/stop, the minimum-duration rule, recovery, day
  aggregation, imports, exports and queue/replay changes. Preserve collected
  regressions when moving files; do not substitute implementation-mirroring tests.

## Database & migrations

- Single SQLite file. Dev: `./data/jobtracker.db`. Frozen app:
  `~/Library/Application Support/JobTracker/jobtracker.db`.
- `Database._init_db()` remains the compatibility entry point; schema work lives
  in [SchemaManager](../jobtracker/core/persistence/schema.py). Migrations use
  additive, idempotent `ALTER TABLE` blocks guarded by `_column_exists`.
  Preserve their order and that pattern; this extraction is not a schema redesign.
- **Before any non-additive / hard-to-reverse schema change** (e.g. renaming the
  legacy `tasks` table — which actually stores *subjects* — to `subjects`):
  - Explain the migration in the PR/description and in code comments.
  - Make it reversible/safe (back up or keep old columns) and test it on a copy.
  - Do **not** do the `tasks` → `subjects` rename casually; it touches every
    query and the import legacy-key handling.
- `last_active_at` (sessions) is a heartbeat timestamp updated ~once/minute while
  active. One UPDATE, no history rows. Keep it cheap.
- Tables: `tasks` (subjects), `sessions`, `todo_tasks` (goals), `settings`,
  `milestones` (FK to `todo_tasks`), `goal_templates`, `weight_measurements`, and
  `weight_uid_aliases`. The `tasks`/`todo_tasks`/`TodoTask` names are legacy and stay for compatibility;
  user-facing terminology is **Subjects** and **Goals**. The **API uses the clean
  names** (`subjects`, `goals`) and maps to the legacy tables — that is how we get
  good nomenclature without the risky table rename.
- Additive columns currently added by `_init_db()`: `tasks.sort_order`,
  `tasks.is_archived`, `sessions.last_active_at`, `todo_tasks.template_id`,
  `todo_tasks.is_focused` (weekly focus, 0/1),
  `goal_templates.recurrence_day` (weekday 1–7 for weekly, day-of-month for
  monthly), `uid` on shared row tables, and `sessions.device_id`. All
  guarded by `_column_exists`.
- **A column added to a synced table needs a matching field on its dataclass.**
  Rows are read as `Model(**dict(row))` over `SELECT *`
  ([database.py](../jobtracker/core/database.py)), so a new column with no field on
  the model raises `TypeError` on *every* read. Add it to
  [models.py](../jobtracker/core/models.py) in the same change.
- **Downgrade hazard:** once a database has been opened by a build that has the
  `uid` columns, an older JobTracker build cannot read it (same `SELECT *` reason).
  Use the installer's recorded pre-update backups to locate a compatible copy;
  never restore one over live data merely to roll back application code.
- The **only** non-additive migration is the `sessions.note` DROP (needs SQLite
  ≥ 3.35; older versions leave the column in place and it is simply unused).
  Don't add a second one casually.

## Logging

- Use `logging.getLogger("jobtracker")`. `setup_logging()` (called from
  `main.py`) writes to a rotating file under the data dir + stderr. **No remote
  logging.** Prefer logging over silent `except Exception: pass`.

## Feature owners and stable facades

Use `python scripts/dev.py context weight.daily-entry` (or `timer.sessions`) to
find owners, contracts and focused checks. The [feature index](generated/feature-index.md)
and [delegation interfaces](generated/interfaces.md) are generated from explicit
source metadata; update the registry when ownership changes. Do not hand-edit
these maps. [Module boundaries](architecture/boundaries.md) describes allowed
imports and compatibility seams.

`Database`, `TrackerService` and `SyncedTrackerService` keep their import paths,
method signatures, state ownership and override dispatch. `Database` owns one
connection and atomic scope; its repositories use that facade. `TrackerService`
owns the active session and composes feature services through the same facade.
Queue-aware write overrides remain explicit in `SyncedTrackerService`. Keep
explicit delegation, private monkeypatch points and injected service use.

Pure core logic belongs in these owners, rather than in widgets:

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

The UI shell is `ui/app.py` (`MainWindow`) with `SubjectsMixin`, `GoalsMixin`,
`GraphsMixin`, and the keyboard/sync controllers under `ui/controllers/`. The
window retains Qt ownership, lifecycle and compatibility method adapters. `MainWindow` owns the single
`TrackerService` as `self.service`; **widgets and mixins never touch `Database`
directly**, they go through the injected service (dialogs take it as a
constructor argument). Page order in the stack is **0 = Goals, 1 = Subjects,
2 = Graphs, 3 = Weight** — existing shortcut handlers keep their indices.
Weight widgets use `list_weight_measurements()`, `upsert_weight()` and
`delete_weight()` through that service. Synced mutations persist their outbox
entry atomically. New manual entries use calendar dates at midnight, independent
of the tracking-day boundary; imported measurements keep their timestamp and
precision. Weight display preferences stay device-local.

Daily weight entry has seven whole-kilogram tiles centered at the rounded
latest reading and ten decimal tiles `.0`–`.9`. Require both selections before
Save; keep the tiles selected until a durable save. On the phone, successful
online **or queued** saves clear only the captured unchanged draft; a newer
in-flight selection and failed/non-durable writes keep their value. Refreshes
on the phone must never preselect tiles or erase an edit. Backfilling an older day must not
move the latest-reading anchor backward. Original imported precision and exact
timestamps survive an edit until the value is deliberately changed. Automatic
Today advances at calendar midnight; explicit dates and unfinished drafts keep
their chosen day. Date, range shifts and UI options stay available without
cluttering the default full-width phone entry.

Phone composition lives in `web/app.js`; features own UI state under
`web/features/`, data transport/mirror/outbox under `web/data/`, and shared
presentation under `web/shared/`. Preserve compatibility re-exports. Features
receive explicit dependencies and never import the composition shell. Keep
request epochs, mutation revisions, draft ownership and the existing DOM for
fullscreen/rotation. Shared fullscreen must work with an iOS viewport fallback,
close on navigation, and restore focus/scroll without reparenting the plot.

`ui/widgets/dialog_utils.py` and `ui/widgets/reorderable_list.py` are shared UI
infrastructure; see the two sections below before adding dialogs or list cards.

Goal editing lives in `ui/widgets/goal_dialog.py` (`GoalDialog`). An unused
`todo_task_dialog.py` used to sit beside it and was deleted — don't recreate it.

Service methods doing the analytics (all logical-day aware, include the live
session): `get_subject_breakdown(grouping=daily|weekly|monthly, days/start/end)`,
`get_agenda_data()`, `get_subject_deletion_summary()`, `get_active_recovery_info()`,
`resolve_recovery()`, and `get_sessions_for_logical_day()`.
`get_daily_subject_breakdown()` is a thin compatibility wrapper. Subject card totals come from `get_subject_stats_map()`
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
row is identified. Both desktop and server import it.

- **Integer row ids are private to one database and never cross the network.**
  Each database keeps its own autoincrement ids for foreign keys, Qt item data,
  and every existing query — which is why none of that code had to change. The
  `uid` (UUID4) is the only identity that travels.
- A uid is minted by an **`AFTER INSERT` trigger**, one per synced table, rather
  than by individual write methods. No write path can forget, including
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

**Automatic startup recovery is device-scoped, and this is load-bearing.**
`TrackerService.recover_open_sessions()` closes stale open sessions — so it may
run at most once for the owning desktop process, never per request or on the
server. Construct with `recover=False` for a service that is only answering a
query; that path adopts the open session without closing anything. Recovery only ever touches
sessions whose `device_id` is this install's **or NULL** (historical rows, imported
backups). A session stamped with another device's id is left completely alone —
that is what stops the Mac from ending a timer the phone is running, and it is the
multi-device form of the existing "never drop an unfinished session" rule.
This restriction concerns automatic startup cleanup, not the user's explicit
`resolve_recovery` operation sent through the ordered outbox and server `/ops`.

## The desktop sync client (`jobtracker/sync/`)

- `state.py` — the local `sync_outbox` and `sync_state` tables. Not synced: they
  describe this machine's relationship with the server, not the user's data.
- `client.py` — HTTP over **stdlib `urllib`** on purpose. The app's only
  third-party dependency is PySide6; keeping it that way means nothing new has
  to be bundled or signed into the .app.
- `engine.py` — push/pull/repair orchestration and stable `SyncEngine` adapters;
  `mirror.py` owns row application/order/column lookup, and `integrity.py` owns
  report verification, UID hashing and clock checks. They share the same database
  connection and column cache, retaining override and monkeypatch dispatch.
  No Qt; tests run against an in-process server (`tests/test_sync_engine.py`).
  **Changes are applied parents-first, not in feed order** (`_apply_order`). The
  feed sorts rows by their most recent change, so a goal edited after its own
  milestone was created hands the milestone over first — and inserting it fails
  on `milestones.goal_id NOT NULL`. Deletes run last and leaves-first. An
  `IntegrityError` rolls back the whole page and its cursor; a full re-download
  may repair it only when the outbox is empty. Keep this as a guarded safety net.
- `service.py` — `SyncedTrackerService`, the service the UI actually gets when
  sync is on. Every mutation is applied locally *and* queued as an operation.
- `qt_worker.py` — the app's only background thread. It opens its **own**
  database connection (sharing one sqlite3 connection across threads is not
  safe); `Database.__init__` enables WAL + `busy_timeout` so the UI thread keeps
  reading while it writes.
- `ui/controllers/sync.py` requests passes at launch, on focus, every five
  minutes, on quit, and shortly after new local outbox entries (one debounced
  request per burst). A clean pass that still leaves work queued gets at most
  three automatic follow-ups; offline or refused passes get none. The
  controller reads bookkeeping only; the worker remains the only network path.
- `settings.py` — the on/off switch and server URL are device-local settings; the
  **token is kept in its own 0600 file, never in the settings table**, because
  `export_data()` copies that table into every backup the user exports.

**Redeploy when server routes, `server/ops.py` or `server/operations/` change.**
A client that emits an operation an older server does not know gets a 404 mid-batch — which is safe (the
queue blocks and nothing is lost) but looks like a sync bug. This has already
happened once. `tests/test_sync_service.py` checks the client's ops exist on the
server *in the test process*; only a redeploy makes that true in production.

Order within one pass is load-bearing: **push, then pull, then verify.** Pushing
first stops a pull from overwriting an edit still sitting in the outbox.

- A protocol/validation refusal blocks the queue and is reported; never drop
  that operation or send later ones around it. A recorded missing-target skip
  and a payload state refusal are acknowledged results, as described above.
- A create op carries the uid the client already minted (`_adopt_uid` on the
  server honours it). Without that the server would invent a second identity and
  the client would pull its own row back as a duplicate.
- **`full_resync()` must never run while the outbox is non-empty** — it deletes
  every local row, which would discard unsent work. `sync()` enforces this.
- Only `SYNCED_SETTING_KEYS` are restored by a resync, so a phone can never
  overwrite this machine's theme or graph range.
- **`/sync/integrity` is cheap by default and deep on request.** Counts catch
  anything added or lost and are checked every pass; `?deep=1` also hashes every
  uid, catching the right *number* of the wrong rows, and runs once every ten
  passes. The client used to ignore the hash entirely while the server computed
  it every time.
- **Shared settings ride along with every `/sync/pull`**, because settings are
  not rows and so never appear in the change feed. Without that, a `day_start_time`
  changed on the phone would only reach the Mac if a full re-download happened to
  be triggered — the Mac would keep bucketing days by the old boundary.

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

Owned by `ui/controllers/keyboard.py`, through the stable
`MainWindow._install_shortcuts()` adapter: all `Qt.WindowShortcut`, all
gated on `_shortcut_focus_allows_navigation()` (no inline dialog, popup, modal,
or text-entry widget focused):

- `←` / `→` — move between the pages in stack order.
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

## The heatmap was removed

There are two graph views, **Stacked Bar and Agenda** — on the desktop and on the
phone. The heatmap (widget, service method, API endpoint and its tests) is gone:
it answered the same question as the bar chart, less precisely. A saved
`graph_view_mode` of `heatmap` folds back to `bar` at launch so nobody lands on a
blank page. Don't reintroduce it without being asked.

`DaySessionsDialog` stays — it is what opens when a day is clicked in either
remaining view, via `get_sessions_for_logical_day()`. It edits / duplicates /
deletes the selected session itself (`apply_session_edits()`,
`duplicate_session()`), and "Open subject history…" hands the same session id to
`ManageSessionsDialog` so it is preselected there.

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
- If an explicitly selected session leaves a day list after a move, clear the
  selection. Repeated move taps must never act on another row by default.
- Manual adds and edits require at least 30 seconds. Reject a short edit before
  mutation so the existing session survives, and validate phone inputs before
  enqueue so an invalid request cannot block later writes. The timer-stop rule
  for discarding a newly timed sub-30-second session remains separate.

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
  (`bar|agenda`; legacy `heatmap` is normalized), `graph_custom_start` /
  `graph_custom_end` (only when range == "custom"), `graph_hour_start` / `graph_hour_end` (agenda window,
  defaults 6 / 23), `graph_fit_horizontal` (default "1"), `graph_autofit_hours`
  (default "0"). Other keys: `theme_fx`, `theme_palette`, `day_start_time`,
  `todo_order_mode` (`manual|deadline`), `device_id`. Only `day_start_time` is
  shared between machines — see `core/sync_policy.py`.
- **`graph_grouping` is a real preference**: `auto` (default) derives the bucket
  size from the range as before, and `daily`/`weekly`/`monthly` override it.
  `resolve_grouping()` applies it. It used to be deleted at launch as obsolete;
  it is not.
- **`days=N` with weekly/monthly grouping snaps to whole buckets** — "60 days"
  becomes "2 whole months" (`_resolve_logical_window`). That is deliberate: a
  rolling window cut into weeks would draw stubby half-height bars at both ends.
  It also means totals are only comparable across bucket sizes when the window is
  given as explicit `start_date`/`end_date`, which is how the desktop presets do
  it. Don't "fix" the apparent missing hours; there is a test pinning this.

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
