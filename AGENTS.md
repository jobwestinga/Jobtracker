# Working in JobTracker

Complete authorized work autonomously through appropriate verification. Make
routine choices without plan approval; reuse existing authorization. Ask only
when essential information, access or required approval prevents progress.
Continue independent work while waiting. Report results and blockers concisely.

Read [engineering rules](docs/engineering-rules.md) for the user's constraints.
Use the [documentation index](docs/index.md) and
`python3 scripts/dev.py context` to list/search feature owners, then
`context FEATURE_ID` for the exact files and checks. Read only the applicable
detail references linked from the engineering rules.

- Keep core/services Qt-free and within [import boundaries](docs/architecture/boundaries.md).
  The server shares desktop services; phone features receive explicit dependencies.
- Preserve facade imports/signatures/override dispatch: `Database`, `TrackerService`,
  `SyncedTrackerService`, `MainWindow` adapters and documented compatibility seams.
- Database owns one connection/atomic scope; TrackerService owns its timer.
  Persist mutations with outbox entries or replay receipts atomically.
- Use stable UIDs and captured local timestamps. Push before pull; never repair
  a mirror with unsent work. `/ops` is the sole server write path.
- Never silently delete unfinished sessions. Automatic startup recovery is
  device-scoped and disabled on the server. Overlapping, long and future sessions are allowed.
- Tracking uses logical days; weight uses calendar dates. Goals stay separate from
  Subjects; recurrence is additive. Preserve drafts, queues and local preferences.
- Offline use must keep working. Only the user's own server receives app data;
  no telemetry/third parties. Never track secrets, private deployment or user data.
- Use [isolated checks](docs/runbooks/testing.md) and [safe installation](docs/runbooks/build-install.md).
  Open sessions/pending writes block updates. Never force-kill or replace bundles
  manually; back up affected databases before migrations.
- After appropriate checks, automatically create one local commit per cohesive
  feature, fix or refactor, including its tests and documentation. Split unrelated
  work into separate commits; stage explicit files/hunks and preserve other work.
  Never push, open a PR, amend or rewrite history unless explicitly requested.

Run `python scripts/dev.py check`. Update feature metadata when owners move;
generated maps are checked from source. Follow the linked runbooks for releases
and recovery; report validation and material limitations.

## Optional shared context

For local work use the engineering rules/index above. If another component's
behavior, shared hosting or a device workflow is relevant or helpful, follow
README's Personal Tech Stack section. The parent ownership table is
`../README.md#where-to-look`; shared rules are `../AGENTS.md` when available.
Read only the relevant owner's entry and rules; do not scan siblings by default.
Standalone project setup stays complete here.
