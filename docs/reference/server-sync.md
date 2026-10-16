# Server and sync details

Read this reference when the task touches these owners. Start with the shared
[engineering rules](../engineering-rules.md) and [task index](../index.md).

## The own-server API

[server/](../../server/__init__.py) imports the same pure core and services as the
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
  `curl --resolve` as described in the [deployment runbook](../runbooks/deploy.md).
- Routine deployment uses [deploy/install.sh](../../deploy/install.sh): an online
  database backup, source and web assets, existing-unit verification and a
  supervised restart. Provisioning system files is a separate `--provision`
  action requiring the configured sudo access; do not replace unrelated files.
- Deployment keeps code, its environment, data, token hashes and backups separate.
  Token files are private (0600). Scheduled SQLite backups use the online backup
  API, including committed WAL data.
- Read routes are owned by [server/routes](../../server/routes/__init__.py).
  `/health` is unauthenticated; data routes require the shared device dependency.
  `/ops` is the only write path. The root static mount is registered **last**.
- [server/runtime.py](../../server/runtime.py) owns the shared state and lock.
  Routers import that owner, never the app composition module. Compatibility
  aliases in `server.app` retain the same objects for dependency overrides and
  existing tests.

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
- [server/ops.py](../../server/ops.py) retains the public registry and `apply_op`
  receipt/transaction boundary. Domain handlers live under
  [server/operations](../../server/operations/__init__.py), with explicit registration.
  Preserve parameter/principal validation, atomic mutation plus receipt and
  replay ordering centrally. Do not add handler discovery side effects or move
  receipt ownership into domain handlers. See the
  [operation map](../generated/operations.md) and [write path](../architecture/write-path.md).

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
