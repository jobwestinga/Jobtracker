# Working in JobTracker

Complete authorized work autonomously through appropriate verification. Make
routine choices without plan approval; reuse existing authorization. Ask only
when essential information, access or required approval prevents progress.
Continue independent work while waiting. Report results and blockers concisely.

Read [engineering rules](docs/engineering-rules.md) for the user's constraints.
Use the [documentation index](docs/index.md) and
`python scripts/dev.py context weight.daily-entry` to find feature owners.

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
  manually; back up affected databases before migrations. No commit/push/PR unless asked.

Run `python scripts/dev.py check`. Update feature metadata when owners move;
generated maps are checked from source. Follow the linked runbooks for releases
and recovery; report validation and material limitations.
