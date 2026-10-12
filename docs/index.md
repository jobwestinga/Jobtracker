# JobTracker documentation

Start with [README](../README.md) for the product and local setup, and
[engineering rules](engineering-rules.md) for development constraints.
[AGENTS.md](../AGENTS.md) keeps the critical invariants close to the repository root.

For a change, run `python scripts/dev.py context weight.daily-entry` or
`python scripts/dev.py context timer.sessions`. The command lists entry points,
owners, contracts and focused tests. Feature IDs and their source ownership live
in [features.json](architecture/features.json); compatibility contracts live in
[contracts.json](architecture/contracts.json).

## Architecture

- [Module boundaries and compatibility](architecture/boundaries.md): layers,
  facade state, imports and delegation.
- [Time model](architecture/time-model.md): local timestamps, logical days,
  calendar weight dates and graph windows.
- [Durable write path](architecture/write-path.md): atomic outbox/receipt writes,
  retries, identity and ordered replication.
- [Decisions](architecture/decisions.md): reasons for the retained design.

## Generated source maps

- [Feature index](generated/feature-index.md): feature entry points and checks.
- [Operation map](generated/operations.md): registered server handlers and clients.
- [Delegation interfaces](generated/interfaces.md): stable facades and owners.

These are generated from explicit source metadata. `python scripts/dev.py check`
checks references, module boundaries, generated maps and offline shell assets.
Use `python scripts/dev.py check --write` after an intentional metadata/source
change, then review the generated diff and rerun the check.

## Refactor deliverables

- [App improvement backlog](product/improvements.md): review fixes and prioritized
  product ideas, with benefits, owners and concrete acceptance checks.

- [Measured refactor report](generated/refactor-report.md): source snapshot,
  ownership changes and verification evidence.
- [Implementation status](generated/implementation-status.md): the proposal's
  backlog items mapped to their implementation and remaining work.

## Runbooks

- [Testing](runbooks/testing.md): isolated Python, native, Node and browser checks.
- [Build and install](runbooks/build-install.md): staged macOS packaging and guarded
  replacement with backups.
- [Deployment](runbooks/deploy.md): own-server updates and phone refresh.
- [Incidents](runbooks/incidents.md): preserve data, diagnose queues and recover safely.
