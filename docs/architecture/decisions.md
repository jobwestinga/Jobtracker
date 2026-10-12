# Architecture decisions

- **Keep compatibility facades.** Existing callers, queue overrides, UI tests and packaging depend on their signatures. Explicit wrappers make extraction reviewable without a coordinated API rewrite.
- **Keep legacy table names.** Subjects use `tasks`; goals use `todo_tasks`; wire names come from [sync_policy](../../jobtracker/core/sync_policy.py). A table rename adds migration risk without improving feature navigation.
- **Keep local wall-clock datetimes.** [Time contracts](time-model.md) pin established behavior. UTC/timezone migration would be a separate data and product change.
- **Keep plain phone ES modules.** Feature factories and split CSS solve the current search and ownership problem without a framework or build toolchain.
- **Keep backup and wire codecs separate.** Backup restore must merge and preserve identities; wire replication strips local integer IDs and resolves foreign keys.
- **Keep explicit operation registration.** [Registration](../../server/operations/__init__.py) is auditable, statically indexed and uses one mutable dispatcher registry. Import-time plugin discovery is unnecessary.
- **Keep the original Weight_graph data as an import source.** App storage owns daily edits and synchronization. Refactoring must not rewrite original scale files.
- **Measure representative work.** [Performance contracts](../../tests/test_performance_contracts.py) and the [browser harness](../../tests/mobile/browser.cjs) use synthetic data and verify tap/refresh behavior. No production data belongs in generated reports.
