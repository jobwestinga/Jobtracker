# JobTracker documentation

Start with [README](../README.md) for the product and local setup, and
[engineering rules](engineering-rules.md) for development constraints.
[AGENTS.md](../AGENTS.md) keeps the critical invariants close to the repository root.

For a change, run `python scripts/dev.py context weight.daily-entry` or
`python scripts/dev.py context timer.sessions`. The command lists entry points,
owners, contracts and focused tests. Feature IDs and their source ownership live
in [features.json](architecture/features.json); compatibility contracts live in
[contracts.json](architecture/contracts.json).

## Find a task without reading the repository

1. Read the compact [engineering rules](engineering-rules.md).
2. Run `python3 scripts/dev.py context` for a feature list, or search with words
   or a file path, e.g. `context weight graph`, `context fullscreen`, or
   `context web/styles/shell.css`. Multiple matches return a compact list.
3. Run `context FEATURE_ID` for exact entry points, supporting styles/helpers,
   contracts and focused checks. Add `--json` for machine-readable metadata.
4. Read the relevant linked detail reference and owners; run the focused checks,
   then the required complete checks. Update metadata if ownership changes.

| Request | Go directly to |
| --- | --- |
| Weight tiles, Save/reset, offline weight edits | [Weight entry](features/weight.md#entry-and-reset) · `weight.daily-entry` |
| Weight line/dots, years, smoothing, fullscreen | [Weight charts](features/weight.md#charts-and-fullscreen) · `weight.plot` |
| Bottom bar, startup, orientation, home screen | [Phone shell](features/weight.md#phone-shell-and-home-screen) · `mobile.shell` |
| Timer, stop/switch, crash recovery | `context timer.sessions` |
| Editing, adding, moving or deleting sessions | `context sessions.edit` |
| Time bars, agenda, current-time line | `context graphs.tracking` |
| Subjects or archive | `context subjects.manage` |
| Goals, milestones, recurring templates | `context goals.milestones` / `context goals.recurrence` |
| Queue, replay, mirror, connection | `context sync.replication` |
| Backup, import/export, restore | `context backups.restore` |
| Mac window, shortcuts, sync lifecycle | `context native.shell` |
| Public source, credentials, private exports, commit guard | [Privacy policy](privacy.md) · `context repository.privacy` |

The feature registry and generated maps are checked against actual paths and
symbols. [Detailed rules](engineering-rules.md#read-only-the-applicable-details)
are split by domain so unrelated implementation history stays out of a task.

## User guides

- [iPhone apps](guides/iphone-apps.md): install Weight and JobTracker, enroll
  each app, reinstall cleanly, sync, exports and rollback.
- [Privacy](privacy.md): local data, public source, commit guards and history.

## Design proposals

- [A4 native Mac overhaul guide](guides/a4-native-mac-overhaul.md): complete
  staged implementation plan, selected designs, recent fixes to preserve,
  feature parity, validation and guarded cutover. Progress and results are in
  the [A4 evidence record](guides/a4-evidence.md); the
  [correctness review](guides/a4-review-2026-10-22.md) records subsequent fixes
  and remaining verification gates.
- [UI foundations and future options](proposals/ui-foundations-and-future-options.md):
  unselected visual, workflow and frontend architecture choices accompanying the
  separate Visual options v2 review.

## Architecture

- [Module boundaries and compatibility](architecture/boundaries.md): layers,
  facade state, imports and delegation.
- [Time model](architecture/time-model.md): local timestamps, logical days,
  calendar weight dates and graph windows.
- [Durable write path](architecture/write-path.md): atomic outbox/receipt writes,
  retries, identity and ordered replication.
- [Native engine host](architecture/native-engine.md): store selection, single
  owner lock, local bridge frames, command allowlist, receipts and lifecycle.
- [Decisions](architecture/decisions.md): reasons for the retained design.

## Detailed references

Read only the one matching the owners you are changing; the engineering rules say
when each applies.

- [Phone implementation](reference/mobile.md): app shell, profiles, offline
  worker, storage, status bar, subject order and graph layout.
- [Server and sync](reference/server-sync.md): API, operations, replay and mirror.
- [Database and migrations](reference/persistence.md): schema, additive columns
  and backups.
- [Native UI and charts](reference/native-ui.md): Mac window, widgets and plots.
- [Feature behaviour](reference/features.md): service rules shared by all clients.

## Generated source maps

- [Feature index](generated/feature-index.md): feature entry points and checks.
- [Operation map](generated/operations.md): registered server handlers and clients.
- [Delegation interfaces](generated/interfaces.md): stable facades and owners.

These are generated from explicit source metadata. `python scripts/dev.py check`
checks references, module boundaries, generated maps and offline shell assets.
Use `python scripts/dev.py check --write` after an intentional metadata/source
change, then review the generated diff and rerun the check.

## Runbooks

- [Testing](runbooks/testing.md): isolated Python, native, Node and browser checks.
- [Build and install](runbooks/build-install.md): staged macOS packaging and guarded
  replacement with backups.
- [Deployment](runbooks/deploy.md): own-server updates and phone refresh.
- [Incidents](runbooks/incidents.md): preserve data, diagnose queues and recover safely.
- [Weight import](runbooks/weight-import.md): one-off import/export of a legacy
  weight history.
