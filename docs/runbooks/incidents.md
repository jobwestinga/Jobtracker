# Incidents and recovery

Preserve user work first. Never delete an unfinished session, clear an outbox,
wipe phone storage or restore an older database to make an error disappear.
Use local logs and read-only inspection; report sensitive deployment details
only through the existing private configuration.

## Collect evidence safely

Record the visible error, app/server version and when it happened. Use
`python scripts/dev.py status` for macOS install guards. It reads existing
databases without importing app services or migrating them. An unexpectedly
missing database is a blocker to investigate, not a new empty database to create.

Desktop logs are under its data directory; server logs belong to its supervisor.
Use `logging.getLogger("jobtracker")` for diagnostic changes, without remote
logging. Do not paste tokens, private hosts or user history into tracked files.
Before repair or migration, make SQLite online backups that include committed
WAL data, and reproduce the issue on isolated copies.

## Timer appears missing or unfinished

Check persisted session identity and device ownership before changing anything.
Server reads must not perform crash recovery. Desktop recovery applies only to
the owning device (or legacy unowned rows), runs at most once for that process
and preserves the primary unfinished session. Stops, heartbeats and queued
switches target the captured session UID, never whichever timer is newest.

An apparently long or overlapping session is valid data. Resolve it through the
existing recovery/session editor with the user's intended end time. Do not
truncate it, reject overlap or create a second entry simply because it looks
unusual. Add a regression for the actual failure before changing the rule.

## Sync queue is blocked

Keep the operation ID, captured timestamps, parameters and order intact.
Investigate authentication, connectivity, unknown operations and the reported
first failure. A newer client may need the matching server deployment; retry
after fixing the cause rather than removing the operation.

Successful receipts, state refusals and recorded missing-target skips are
acknowledged results. Protocol/validation failures stop the ordered queue.
Never send later operations around the failed item. A dropped acknowledgement
must replay the same operation ID and receive the saved result.

Do not run full resync while pending writes exist. Push precedes pull; a feed
page and cursor commit together, with parent-first upserts and leaf-first
deletes. When the queue is empty, the existing guarded repair path can replace
the read mirror. Preserve device-local settings and credentials. Investigate
clock differences and TLS certificate trust; never disable TLS verification.

## Weight entry or graph looks wrong

Weight uses calendar dates, independently of the tracking-day boundary. Same-day
manual saves can coalesce to a canonical UID; old UIDs remain aliases for
retry/edit/delete. Imported scale/historic multi-readings and their original
precision/timestamps remain valid. Do not collapse all imported rows into one
reading or discard alias bookkeeping during restore.

Before retrying a save, distinguish a durable queued result from a storage or
validation failure. Refresh must preserve drafts; a newer draft entered during
a save must survive that save's response. A remote deletion skip preserves the
chosen value for a deliberate new save. Reproduce midnight, stale-response or
offline races with the isolated browser/service tests.

For time graphs, inspect the logical day and current range before repairing
totals. Bars attribute whole sessions to their start day; agendas clamp to a
logical-day column. Weekly/monthly rolling windows intentionally align to full
buckets. Compare explicit matching dates when comparing grouping totals.

## App update or deployment failed

Follow [build/install](build-install.md) or [deployment](deploy.md); keep their
backup and status records. Queued macOS updates wait for all open sessions and
pending writes to finish. Do not force-kill the app or bypass the idle checks.
The installer preserves the previous bundle and database backups; a failed
replacement rolls back code only. A downgraded app may not understand a newer
schema, so validate compatibility against a copy before using an older bundle.

A server rollback restores compatible code/assets, not an old live database.
Check health, service configuration, dependencies and operation support. Do not
change other hosted services or private-network DNS to recover JobTracker.
For a stale phone shell, reconnect and reload normally without wiping storage.

Restore user data only as an explicit data-recovery action after inspecting
backups and the current database on copies. Full JSON/ZIP import is the supported
restore path; CSV is for reading/export. With sync enabled, backup restoration
is an atomic queued operation preserving canonical identities and relationships.
Do not copy a backup database over an active installation.
