# Write and replication contracts

## Ordered outbox

Desktop synced writes enter [SyncedTrackerService](../../jobtracker/sync/service.py). One [Database.atomic](../../jobtracker/core/database.py) scope stores the business mutation and its operation in the same connection. Nested services call the facade so subclass overrides keep recording operations. Component extraction must never bypass this dispatch or create a second connection.

Phone writes enter the [durable outbox](../../web/data/outbox.js). Storage succeeds before a request is sent. Each operation receives a stable `op_id`; only acknowledged IDs are removed, and additions made during a request are preserved. Credential changes cannot silently discard pending edits. [Storage keys](../../web/shared/storage.js) and [credential transitions](../../web/data/credentials.js) retain the existing on-disk formats.

Both clients require acknowledgements to match the submitted ordered prefix;
malformed or reordered receipts leave that batch intact. Phone transport rejects
stale error responses as well as successes after a credential change. Setup
refuses a different saved token while writes are pending, and disconnect reports
failure if token removal fails. Read mirrors have a credential tag without
copying the token; a different account or untagged legacy cache cannot restore
private history. Rejecting an optional cache never clears the outbox.

A separate `jobtracker.outbox-owner` tag binds pending phone operations to the
credential that saved them. Ownership is stored before a new queue write under
the queue lock, and every drain checks it. A token change in another tab cannot
replay the original draft under that tab's credential; reconnecting the original
token retries the same operation IDs. Transport uses the captured credential
for each POST. An older untagged queue binds on its first replay using the
existing token; its saved operation JSON and server request format are unchanged.

Initial storage failure rejects the action. Failures after the durable enqueue,
including failure to save an acknowledgement, keep the original operation for
retry. The [action runner](../../web/data/actions.js) preserves that saved result
even if projection, rendering or a completion callback fails. Session forms
permit another submission only after a failure before persistence; otherwise a
second tap could create a new identity for work already saved. These are local
UI outcomes and do not change the server's mutation wire format. The shared
[sheet action guard](../../web/shared/sheet.js) applies this to session, goal,
template and settings forms; a stale handler cannot submit after its sheet closes.

## Receipt and principal

`POST /ops` is the server's only synced write endpoint. [Ordered dispatch](../../server/routes/ops.py) stops at the first failure and reports the acknowledged prefix. [apply_op](../../server/ops.py) commits the mutation and receipt together. Receipt identity covers operation name, parameters, originating device and authenticated principal; reusing an ID for another request returns a conflict.

If the server commits a 75-minute session but the reply is lost, the client keeps the operation. Retrying the same request returns its saved receipt with `replayed: true`; it creates no second session. [Shared outcome cases](../../tests/contracts/operation_outcomes.json) and [API tests](../../tests/test_operation_contracts.py) verify this together with refusal, skip, daily weight coalescing and partial batch failure.

The [closed-session retry trace](../../tests/test_sync_safety.py) also compares
both stores with independent expected rows. Six cases combine offline creates,
moves, shifts, copies and deletes with a temporary failure after a mutation,
received or lost prefix acknowledgements, reopening the pending store and later
offline edits. They check transaction rollback, original operation/entity UIDs,
microsecond endpoints, logical-day copying, replay receipts and settled queues
without mirror repair.

## Compatible outcomes

| Category | Existing transport result | Queue behavior |
| --- | --- | --- |
| Applied | HTTP 200, entry in `applied`, `replayed: false` | Acknowledge this ID. |
| Replay | HTTP 200, saved result, `replayed: true` | Acknowledge this ID; no new mutation. |
| Deleted target | HTTP 200, `skipped: true` and `reason` | Acknowledge; a retry cannot restore the target. |
| State refusal | HTTP 200, existing domain result such as `completed: false` | Acknowledge; refresh the authoritative entity. |
| Invalid request/conflict | HTTP 4xx, `failed_op_id`, `failed_index`, acknowledged prefix | Keep failed and later entries in order. |
| Retryable transport failure | Network error, timeout, HTTP 408/429/5xx | Keep unacknowledged entries for retry. |

These names document existing JSON; they do not introduce a new wire envelope. [ApiError](../../web/data/errors.js) retains transport classification. [The operation matrix](../generated/operations.md) records server handlers, desktop queue methods and phone projections. Phone projections are provisional display state; the server remains the rule authority.

## Mirror repair

[SyncEngine](../../jobtracker/sync/engine.py) orchestrates push, pull and integrity verification in that order. [mirror.py](../../jobtracker/sync/mirror.py) applies rows using the existing connection and column cache; [integrity.py](../../jobtracker/sync/integrity.py) owns hash/clock/report checks. Overrides on the engine remain dispatchable.

An empty outbox is required before full mirror replacement. Never repair or restore over unsent work. Row identities are UIDs; integer IDs are local. [sync_policy](../../jobtracker/core/sync_policy.py) owns table/wire translation and foreign-key mapping. Backup import/export uses [BackupCodec](../../jobtracker/core/persistence/backup.py); network encoding uses `WireCodec`. They have different trust and identity boundaries.

Backup export reads all tables in one SQLite read snapshot, so a concurrent
sync commit cannot mix earlier subjects with later sessions or aliases. Its
savepoint leaves an existing caller transaction uncommitted and releases the
snapshot on failure. Import retains its separate atomic merge and validation.

## Timer identity

[TrackerService](../../jobtracker/services/tracker_service.py) owns active subject/session state; [TimerService](../../jobtracker/services/timer_service.py) owns timer use cases. Stops, switches, heartbeats and recovery must target the exact persisted session and preserve device ownership. Reads never abandon an unfinished timer. Recovery resumes the primary session and preserves foreign-device sessions. The [timer reliability tests](../../tests/test_timer_reliability.py) and [sync safety tests](../../tests/test_sync_safety.py) cover crash/replay/race behavior.
