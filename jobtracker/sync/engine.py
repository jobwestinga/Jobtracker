"""The sync engine: flush the outbox, pull the feed, verify, repair.

No Qt in here — this is plain Python over two databases, so the whole thing is
testable headlessly against an in-process server.

**The mirror is a cache, never the truth.** That single decision is what makes
this safe: if anything about the local copy looks wrong, the fix is always to
throw it away and download the server's state again. There is no merge, so there
is no merge to get wrong.

Order of a sync pass, and why:

1. **Push first.** Local work reaches the server before we ask what changed,
   so a pull can't overwrite an edit that was still sitting in the outbox.
2. **Pull the feed** and apply it by uid — an upsert per changed row, a delete
   per removed one.
3. **Verify** with the server's per-table count+hash. A mismatch means the mirror
   drifted, and the repair is a full re-download. Drift gets corrected on its own
   rather than quietly rotting.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core import sync_policy
from ..core.database import Database
from . import integrity, mirror, state
from .client import SyncClient, SyncError

logger = logging.getLogger("jobtracker")


@dataclass
class SyncResult:
    """What one pass did. Rendered directly in the Settings status line."""

    ok: bool = False
    offline: bool = False
    pushed: int = 0
    applied: int = 0
    deleted: int = 0
    repaired: bool = False
    pending: int = 0
    blocked_reason: Optional[str] = None
    error: Optional[str] = None
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.offline:
            return (
                f"Offline — {self.pending} change(s) waiting"
                if self.pending
                else "Offline"
            )
        if self.blocked_reason:
            return f"Stuck: {self.blocked_reason}"
        if not self.ok:
            return f"Sync failed: {self.error or 'unknown error'}"
        bits = []
        if self.pushed:
            bits.append(f"sent {self.pushed}")
        if self.applied:
            bits.append(f"updated {self.applied}")
        if self.deleted:
            bits.append(f"removed {self.deleted}")
        if self.repaired:
            bits.append("re-downloaded")
        status = f"{self.pending} change(s) waiting" if self.pending else "Up to date"
        return status + (f" ({', '.join(bits)})" if bits else "")


class SyncEngine:
    def __init__(self, database: Database, client: SyncClient, device_id: str = ""):
        self.db = database
        self.client = client
        self.device_id = device_id
        # PRAGMA table_info was being run once per applied row. The schema does
        # not change while the app runs, so read it once per table.
        self._column_cache: dict[str, list[str]] = {}
        state.install(self.db.connection)

    # ── applying server rows to the mirror ──────────────────────────────
    def _upsert(self, api_table: str, uid: str, wire_row: dict, codec=None, heartbeat=None) -> None:
        """Apply a row through the mirror owner, preserving column overrides."""
        mirror.upsert(
            self.db, api_table, uid, wire_row, codec, heartbeat,
            device_id=self.device_id, column_names=self._columns,
        )

    def _delete(self, api_table: str, uid: str, codec=None) -> bool:
        return mirror.delete(self.db, api_table, uid, codec)

    @staticmethod
    def _apply_order(change: dict) -> tuple:
        return mirror.apply_order(change)

    def _columns(self, table: str) -> list[str]:
        return mirror.columns(self.db, table, self._column_cache)

    # ── the three phases ────────────────────────────────────────────────
    def push(self, result: SyncResult) -> bool:
        """Send queued operations in order. Stops at the first refusal.

        Returns False when the queue is blocked, so the caller can skip the pull
        and leave the mirror alone until the user resolves it.
        """
        while True:
            queued = state.pending(self.db.connection)
            if not queued:
                return True
            try:
                response = self.client.send_ops(queued, device_id=self.device_id)
            except SyncError as exc:
                # A failed ordered batch can still have a successful prefix.
                # Acknowledge that prefix so the actual failing entry becomes
                # the head, while every unacknowledged entry keeps its op_id.
                if isinstance(exc.body, dict):
                    self._acknowledge(queued, exc.body.get("applied", []), result)
                raise
            acknowledged = self._acknowledge(queued, response.get("applied", []), result)
            if acknowledged != len(queued):
                raise SyncError("server did not acknowledge the complete operation batch", status=502)

    def _acknowledge(self, queued: list[dict], applied: list, result: SyncResult) -> int:
        if not isinstance(applied, list) or len(applied) > len(queued):
            raise SyncError("server returned an invalid operation acknowledgement", status=502)
        if any(not isinstance(entry, dict) or entry.get("op_id") != queued[index]["op_id"]
               for index, entry in enumerate(applied)):
            raise SyncError("server acknowledged operations out of order", status=502)
        with self.db.atomic():
            for entry in queued[:len(applied)]:
                state.drop(self.db.connection, entry["id"])
        result.pushed += len(applied)
        return len(applied)

    def pull(self, result: SyncResult) -> bool:
        cursor = state.last_seq(self.db.connection)
        while True:
            response = self.client.pull(cursor)
            changes = response.get("changes", [])
            if response.get("head") is not None and int(response["head"]) < cursor:
                # Restoring an older server backup can keep all the same uids
                # but rewind edits and the feed. Count/hash verification alone
                # cannot detect that every row's contents became older.
                result.messages.append("server history was restored; re-downloading")
                return self.full_resync(result)
            next_cursor = int(response.get("seq", cursor))
            if next_cursor < cursor or (response.get("more") and next_cursor <= cursor):
                raise SyncError("server returned a non-advancing change cursor", status=502)
            # One codec for the whole page: foreign keys resolve from a map
            # loaded once instead of a query per row.
            applied = deleted = 0
            with self.db.atomic():
                # The UI can create new work while this network call is in
                # flight. Hold the write lock across this check and the apply,
                # and postpone the pull instead of overwriting those edits.
                if state.pending_count(self.db.connection):
                    result.messages.append("new local edits are waiting; pull postponed")
                    return False
                codec = sync_policy.WireCodec(self.db)
                for change in sorted(changes, key=self._apply_order):
                    if change.get("op") == "delete":
                        if self._delete(change["table"], change["uid"], codec):
                            deleted += 1
                    else:
                        row = change.get("row")
                        if row:
                            self._upsert(change["table"], change["uid"], row, codec)
                            applied += 1
                for key, value in (response.get("settings") or {}).items():
                    if key in sync_policy.SYNCED_SETTING_KEYS and value:
                        if self.db.get_setting(key) != value:
                            self.db.set_setting(key, value)
                            result.messages.append(f"{key} is now {value}")
                # Applying a page and moving its cursor are one transaction.
                state.set_value(self.db.connection, state.LAST_SEQ, next_cursor)
            result.applied += applied
            result.deleted += deleted
            cursor = next_cursor
            if not response.get("more"):
                return True

    def verify(self, result: SyncResult, deep: bool = False) -> bool:
        """Fetch an integrity report and verify it through overridable callbacks."""
        report = self.client.integrity(deep=deep)
        return integrity.verify_report(
            self.db, report, result,
            check_clock=self._check_clock, uid_hash=self._uid_hash,
        )

    def _uid_hash(self, table: str) -> str:
        return integrity.hash_uids(self.db, table)

    @staticmethod
    def _check_clock(report: dict, result: SyncResult) -> None:
        integrity.check_clock(report, result, clock=datetime.now)

    def full_resync(self, result: SyncResult) -> bool:
        """Throw the mirror away and rebuild it from the server.

        Safe precisely because the mirror is not authoritative — but it must not
        run while the outbox still holds work, or unsent local changes would be
        wiped. This method enforces that under the database write lock.
        """
        # Check before the request, then again under the write lock: another
        # thread can edit the mirror while the snapshot is downloading.
        if state.pending_count(self.db.connection):
            result.messages.append("outbox has unsent edits; re-download postponed")
            return False
        snapshot = self.client.snapshot()
        for table in sync_policy.SYNCED_TABLES:
            if not isinstance(snapshot.get(sync_policy.API_NAMES[table]), list):
                raise SyncError("server snapshot is incomplete; local history was preserved", status=502)
        applied = 0
        with self.db.atomic():
            if state.pending_count(self.db.connection):
                result.messages.append("new local edits are waiting; re-download postponed")
                return False
            cur = self.db.connection.cursor()
            heartbeats = {
                row["uid"]: row["last_active_at"]
                for row in cur.execute(
                    "SELECT uid, last_active_at FROM sessions WHERE end_time IS NULL "
                    "AND (device_id IS NULL OR device_id = ?)", (self.device_id,)
                )
            }
            for table in reversed(sync_policy.SYNCED_TABLES):
                cur.execute(f"DELETE FROM {table}")
            codec = sync_policy.WireCodec(self.db)
            for table in sync_policy.SYNCED_TABLES:
                for wire_row in snapshot[sync_policy.API_NAMES[table]]:
                    uid = wire_row.get("uid")
                    if not isinstance(uid, str) or not uid:
                        raise SyncError("server snapshot contains a row without an identity", status=502)
                    self._upsert(
                        sync_policy.API_NAMES[table], uid, wire_row, codec,
                        heartbeat=heartbeats.get(uid) if table == "sessions" else None,
                    )
                    applied += 1
            for key, value in (snapshot.get("settings") or {}).items():
                if key in sync_policy.SYNCED_SETTING_KEYS:
                    self.db.set_setting(key, value)
            state.set_value(self.db.connection, state.LAST_SEQ, int(snapshot.get("head", 0)))
        result.applied += applied
        result.repaired = True
        return True

    def _due_for_periodic_check(self, every: int = 10) -> bool:
        """True once every ``every`` quiet passes, so drift cannot hide forever."""
        try:
            count = int(state.get(self.db.connection, state.QUIET_PASSES, "0") or 0)
        except ValueError:
            count = 0
        count += 1
        if count >= every:
            state.set_value(self.db.connection, state.QUIET_PASSES, "0")
            return True
        state.set_value(self.db.connection, state.QUIET_PASSES, str(count))
        return False

    # ── one full pass ───────────────────────────────────────────────────
    def sync(self, verify: bool = True) -> SyncResult:
        result = SyncResult()
        try:
            self.push(result)
            try:
                pulled = self.pull(result)
            except sqlite3.IntegrityError:
                # A parent can land on a later feed page. Roll the page back,
                # keep its old cursor, and rebuild from a complete snapshot.
                logger.warning("Feed page is missing a dependency; re-downloading")
                pulled = self.full_resync(result)
            if not pulled:
                result.ok = True
                result.pending = state.pending_count(self.db.connection)
                return result
            # Counts are compared every pass; the uid hash only occasionally,
            # since that makes the server read every uid in every table.
            if verify and not self.verify(result, deep=self._due_for_periodic_check()):
                logger.warning("Mirror drifted from server; re-downloading")
                if state.pending_count(self.db.connection) == 0:
                    self.full_resync(result)
                else:
                    result.messages.append(
                        "drift detected but the outbox is not empty; "
                        "leaving the mirror alone this pass"
                    )
            result.ok = True
            state.set_value(
                self.db.connection, state.LAST_SYNC_AT, datetime.now().isoformat()
            )
            state.set_value(self.db.connection, state.LAST_ERROR, "")
        except SyncError as exc:
            result.ok = False
            result.error = str(exc)
            result.offline = exc.offline
            if not exc.offline:
                # A refusal means the operation at the head of the queue will
                # never succeed on its own. Surface it instead of retrying
                # forever; the queue stays intact so nothing is lost.
                result.blocked_reason = str(exc)
                head = state.pending(self.db.connection, limit=1)
                if head:
                    state.record_failure(self.db.connection, head[0]["id"], str(exc))
            state.set_value(self.db.connection, state.LAST_ERROR, str(exc))
            logger.info("Sync did not complete: %s", exc)
        except Exception as exc:  # noqa: BLE001 - never let sync crash the app
            result.ok = False
            result.error = str(exc)
            logger.exception("Sync failed unexpectedly")

        result.pending = state.pending_count(self.db.connection)
        return result
