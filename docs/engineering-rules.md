# Engineering rules

Read this compact policy for every task. Then use the [task index](index.md) or
`python3 scripts/dev.py context <words-or-feature-id>` to open only the relevant
owners, contracts and tests. With no query, `context` lists features; `--json`
returns the matching metadata. [README](../README.md) describes the product.

## Read only the applicable details

These references preserve the detailed constraints and reasons. Read the rows
that match the work before editing their owners; unrelated references can wait.

| Work touches | Required detail reference |
| --- | --- |
| Phone UI, refresh, drafts, offline shell, fullscreen | [Phone](reference/mobile.md) |
| Server routes, operations, sync, identity, outbox | [Server and sync](reference/server-sync.md) |
| Schema, database facade, import/export, migrations | [Persistence](reference/persistence.md) |
| Feature services, goals, weight entry, ownership/facades | [Feature contracts](reference/features.md) |
| Native widgets, shortcuts, session actions, chart/theme styling | [Native UI](reference/native-ui.md) |

Weight tasks also have a [small end-to-end owner map](features/weight.md).

## Working autonomously

- Complete the authorized work and appropriate verification without asking the
  user to approve routine plans or equivalent implementation choices.
- Reuse authorization already given in the conversation. Assume reasonable
  optional details and report material assumptions in the result.
- Ask only when essential information, access or a required approval prevents
  progress. Continue independent work while a required answer is pending.
- Report completed changes, meaningful validation and remaining blockers
  concisely. Create local commits under the policy below; publishing is separate.

## What this app is

- A **self-hosted, personal time tracker**. A macOS desktop app (PySide6 /
  Qt 6 Widgets + SQLite) and an iPhone web client talking to **the user's own
  server**. Single user, no third parties.
- **Subjects** are the timed entities — you start/stop a timer on a subject and
  it records sessions. **Goals are a separate, outcome-focused area** with
  descriptions and milestones. They are intentionally **not** connected to
  timed subjects.
- Each self-hosted deployment serves one person. Keep workflows simple.
- Architecture layers: `core` (config, models, database, themes, timeutils,
  sync_policy, logging) → `services` (TrackerService) → `ui` (PySide6 widgets).
  `core` and `services` contain **no Qt imports** and must stay that way: the
  server imports and runs the same `TrackerService` the desktop app runs, which
  is what keeps the two from ever disagreeing about a rule.

## Hard rules (do not break)

- **Commit cohesive work automatically after verification.** Keep each feature,
  fix or refactor in its own local commit with the related tests/documentation.
  Stage explicit files or hunks, preserve unrelated work, and use descriptive
  messages. Do not amend or rewrite history, push, or open PRs unless asked.
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

## Logging

- Use `logging.getLogger("jobtracker")`. `setup_logging()` (called from
  `main.py`) writes to a rotating file under the data dir + stderr. **No remote
  logging.** Prefer logging over silent `except Exception: pass`.

## Style

- Concise, direct solutions over abstractions. Match surrounding code.
- Don't rewrite the whole app or do large UI rewrites for cleanup. Prefer small,
  tested, reversible changes. Run the relevant tests after changes.
