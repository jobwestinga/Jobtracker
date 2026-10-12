# Refactor implementation status

Implemented from the [MOD/REF proposal](<../../MOD/REF PLAN.pdf>) dated 11 October 2026. The original PDF remains the proposal snapshot. Current ownership is maintained in [features.json](../architecture/features.json), checked by source-derived [feature](feature-index.md), [operation](operations.md) and [interface](interfaces.md) maps.

| Plan item | Delivered owner / evidence |
| --- | --- |
| R01 | [AGENTS](../../AGENTS.md) and thin [CLAUDE](../../CLAUDE.md) point to one [engineering policy](../engineering-rules.md). |
| R02 | [Documentation index](../index.md) routes architecture, task context and release/recovery work. |
| R03 | [Feature manifest](../architecture/features.json) maps entry points, rules, persistence, operations, tables, routes, tests and related features. |
| R04 | [Reference checker](../../scripts/check_references.py) validates symbols/IDs/links and deterministic generated maps; [broken examples](../../tests/test_repository_checks.py) fail. |
| R05 | [dev.py context](../../scripts/dev.py) prints a focused task packet for a stable feature ID. |
| R06 | [Time model](../architecture/time-model.md) and shared local-time boundary fixtures distinguish tracking and weight dates. |
| R07 | [Write-path contract](../architecture/write-path.md) records commit, outbox, principal, receipt and mirror ownership with a lost-ack example. |
| R08 | [Boundary checker](../../scripts/check_boundaries.py) enforces layer direction and detects eager Python/static JavaScript cycles. |
| R09 | [Operation matrix](operations.md) derives registered handlers, desktop queue calls and all phone projection/server-authority classifications. |
| R10 | [Compatible outcomes](../architecture/write-path.md#compatible-outcomes) retain existing success/replay/skip/refusal/failure JSON; shared fixtures execute real server and phone transport paths. |
| R11 | [Storage helpers](../../web/shared/storage.js) centralize selected keys/account tags; [credentials](../../web/data/credentials.js) preserve pending-write guards. |
| R12 | [Facade contract](../architecture/boundaries.md#facades-and-state-owners) and [delegation map](interfaces.md) identify state/transaction owners; extraction regression tests verify dispatch. |
| R13 | [Development dispatcher](../../scripts/dev.py) routes checks/tests/build/status; [guarded installer](../../scripts/install_macos.py) retains active-session/outbox protection. |
| R14 | Phone [composition shell](../../web/app.js) delegates feature factories; [lifecycle tests](../../tests/mobile/feature-lifecycle.test.mjs) retain drafts and stale-response protection. |
| R15 | Phone styles split into tokens/shell/controls and feature styles; [browser harness](../../tests/mobile/browser.cjs) verifies narrow and landscape layouts. |
| R16 | Server [composition](../../server/app.py) explicitly includes six router groups before static mounting; [structural tests](../../tests/test_server_structure.py) verify shared auth/runtime. |
| R17 | Server [domain registry](../../server/operations/__init__.py) registers handlers explicitly; [dispatcher](../../server/ops.py) retains atomic receipts and ordered execution. |
| R18 | Subject/goal/recurrence feature services delegate behind [TrackerService](../../jobtracker/services/tracker_service.py); [service tests](../../tests/test_service_structure.py) verify synced override dispatch. |
| R19 | [AnalyticsService](../../jobtracker/services/analytics_service.py) owns aggregation/window resolution; grouping/agenda/performance regressions retain established totals. |
| R20 | [Database](../../jobtracker/core/database.py) delegates schema/connection/entity repositories; [persistence tests](../../tests/test_persistence_structure.py) verify shared connection and atomic rollback. |
| R21 | [MainWindow](../../jobtracker/ui/app.py) delegates keyboard/sync controllers while retaining Qt ownership and lifecycle. |
| R22 | [TimerService](../../jobtracker/services/timer_service.py) extracted after transaction/identity seams; timer/recovery/sync tests preserve active state and exact session targeting. |
| R23 | Native UI regressions grouped under `tests/ui`; common native/API/sync helpers moved into [tests/support](../../tests/support/__init__.py). Collected-test imports are rejected. |
| R24 | [Time boundaries](../../tests/contracts/time_boundaries.json), [operation outcomes](../../tests/contracts/operation_outcomes.json) and weight-math fixtures are consumed by Python and Node. |
| R25 | Removed proven-unused phone heatmap CSS and duplicate test/helper bodies; documented compatibility entry points remain where callers use them. |
| R26 | [CI checks](../../.github/workflows/verify.yml), reference drift checks, [offline asset coverage](../../scripts/check_web_assets.py), [deployment filter regressions](../../tests/test_deploy_assets.py) and [reproducible measurements](../../scripts/measure_refactor.py) added. |

No schema/table rename, wire-format migration, new app framework or replacement of existing public service interfaces was introduced. Composition files became smaller; extracted services/repositories retain the same facade references so queued mutations stay complete. The measured [report](refactor-report.md) records source sizes and synthetic performance without user data.

Release verification uses the [testing](../runbooks/testing.md), [build/install](../runbooks/build-install.md) and [deployment](../runbooks/deploy.md) runbooks. Installation checks all open sessions and queued writes immediately before replacement, and defers automatically when work is active.

## Verification snapshot — 11 October 2026

- Full Python suite: 900 passed; two additional deployment-filter regressions passed after the live asset check identified an overly broad `data/` exclusion. The filter now preserves public `web/data` modules while excluding private repository data.
- Node contracts: 81 passed. Real-browser checks passed at four viewport sizes, including network-offline shell/reload/reconnect, weight drafts/save clearing, fullscreen rotation, timer replay and agenda timing.
- Deliberately broken references, imports, package cycles, dynamic asset imports, Markdown links and stale reexports are rejected by regression tests. Generated maps and the offline shell inventory pass their checks.
- Signed packaged app launched with an isolated database and synchronization disabled, then installed through the idle guard and relaunched. Both existing databases retained their stored identities; original Weight_graph source digests remained unchanged.
- Own-server deployment passed health, authentication, private `no-store` headers and byte-for-byte checks of all 51 public assets. Database integrity, schema and stored identities remained intact.

## Session-control follow-up — 11 October 2026

Quick Add now includes 90 minutes on both clients. Custom duration buttons toggle
4h, 2h, 1h, 30m, 15m, 10m and 5m, with a visible sum before confirmation.
Shared session controls add ±1-day moves while retaining the session UID,
duration, local clock time and original fractional endpoints.

The phone [duration picker](../../web/features/sessions/duration-picker.js),
[time calculations](../../web/features/sessions/duration.js) and
[action runner](../../web/data/actions.js) have separate owners. The action runner
and outbox preserve durability after acknowledgement-storage or display failures,
so forms cannot retry already saved work with a new identity. Short manual edits
are refused before mutation; moving a row out of a day list clears its selection
instead of targeting another row. These rules are linked from the feature map.

- Full Python suite: **936 passed**; mobile Node suite: **93 passed**.
- Full browser checks passed at 320/390px, landscape and laptop widths; final
  phone layout checks confirmed readable dates, large duration/move controls,
  offline/reconnect, dropped acknowledgements, initial storage failure, exact
  timestamps, minimum-duration boundaries and all shared session editor paths.
- The signed Mac package passed isolated normal-window launch and normal quit,
  then installed with backups through the idle guard and relaunched. Both
  databases retained stored identities and integrity; weight source files stayed
  unchanged.
- Own-server health, authentication, private cache headers and all **54** public
  asset hashes passed live verification. Schema, stored IDs and active sessions
  remained intact. No live data writes were used for testing.

## Overall app review — 12 October 2026

The [improvement backlog](../product/improvements.md) records 16 prioritized
ideas, with feature owners and acceptance checks. This review also delivered
concrete reliability fixes rather than only proposals:

- Shared phone sheet mutation guards retain failed drafts and prevent duplicate
  goal, template, settings and session saves. Milestone draft revisions and
  pending toggle locks preserve newer input and prevent repeated inversions.
- Fractional tracking boundaries remain exact in phone and native settings;
  all native dismissal paths restore a preview, while imported preferences
  become the new baseline. Archived editing and stale timer prompts are fixed.
- Native goal completion saves before its cosmetic fade. Feedback animations
  retain/reuse bounded wrappers, with explicit garbage-collection and Qt cleanup
  stress regressions for completion, cancellation and owner destruction.
- Phone outboxes validate ordered acknowledgement prefixes, preserve operations
  after storage failure, and bind pending work to its saved credential. Foreign
  drafts are excluded from display; another tab's token change and cold startup
  route mismatched queues to original-connection recovery. Original operation
  JSON and mutation requests stay compatible.
- Weight imports/edits retain fractional timestamps with legacy import UUIDs.
  Canonical daily aliases survive partial acknowledgements and date correction;
  the authenticated weight read adds optional alias metadata. Plot validation
  and naive calendar coordinates reject corrupt readings and avoid DST coercion.
- Backup exports use a single read snapshot during concurrent sync. Git filters
  retain public `web/data` code while excluding private root data.

Verification: **978 Python tests** and **128 Node tests** passed. Both browser
harnesses passed, including 320/390px, landscape and laptop layouts, offline
replay, settings failures, cross-tab credential changes and queue recovery.
References, module boundaries, offline assets and diff checks pass. The build,
install and live deployment verification follow the existing guarded runbooks.

Release completed: the signed package passed isolated normal-window launch and
normal quit, then installed with backups through the idle guard and relaunched.
Both databases retained integrity and stored identities; original Weight_graph
inputs stayed unchanged. Live own-server health, authentication, private cache
headers, weight alias support and all **54 public asset hashes** passed. Server
schema, stored identities and active sessions were preserved. Tests used no
live data writes.

## Selected work release — 12 October 2026

The [selected backlog items](../product/improvements.md#selected-work) are
released: persistent sync status on both clients, phone Subject Quick Add and
the accessibility pass. Follow-up review changes in the same release:

- The phone status line is a button that opens the detailed Settings view; a
  confirmed sync from an earlier day names its date. The Mac
  [sync controller](../../jobtracker/ui/controllers/sync.py) pushes newly queued
  local edits after a short debounce, with at most three follow-ups when a
  clean pass still leaves work queued and none after offline or refused passes.
- A saved quick add confirms its range and, before the day boundary, the
  tracking day it counts towards. Subject edit/add/archive sheets use the shared
  durable sheet action, so failed persistence keeps the draft and repeated taps
  queue one operation. A double tap on a subject cannot start and then stop the
  new timer under the 30-second rule.
- Subject colour swatches and list-order controls are named groups/toggles.
  Mac Settings groups Reduce motion with the appearance options.
- A locale-dependent browser assertion now accepts either day/month order.

Verification: **1007 Python tests**, **139 Node tests** and all four browser
harnesses passed, including the new receipt, subject-draft retry, double-tap,
status-button, Subjects/Sessions reflow and debounced-push regressions.
References, boundaries, offline assets and diff checks pass. One earlier full
run stalled in a native test while a PyInstaller build and packaged launch ran
concurrently; it did not reproduce in the UI directory alone or in a clean full
rerun.

Release: the signed package passed an isolated launch and normal quit, then
installed through the idle guard with backups and relaunched; both databases
kept integrity and stored identities, Weight_graph inputs were unchanged, and
the relaunched app synced with an empty queue. The own server passed health,
authentication, `no-store` headers and all **55 public asset hashes**
(`jobtracker-v10`); schema, stored identities and active sessions were
preserved. Tests used no live data writes. VoiceOver/iPhone Safari remain
manual checks.
