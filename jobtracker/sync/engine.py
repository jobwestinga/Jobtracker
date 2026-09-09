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

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core import sync_policy, timeutils
from ..core.database import Database
from . import state
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
        return "Up to date" + (f" ({', '.join(bits)})" if bits else "")


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
    def _upsert(self, api_table: str, uid: str, wire_row: dict, codec=None) -> None:
        """Write one server row into the mirror, keyed by uid.

        Foreign keys arrive as uids and are translated back to this database's
        local ids. A reference we have not seen yet resolves to NULL rather than
        failing; the row is corrected when its parent arrives, and the integrity
        check catches anything still wrong afterwards.
        """
        table = sync_policy.TABLE_FOR_API_NAME.get(api_table)
        if table is None:
            logger.warning("Ignoring unknown table from server: %s", api_table)
            return

        resolver = codec if codec is not None else self.db
        row = sync_policy.from_wire(resolver, table, dict(wire_row))
        row["uid"] = uid

        columns = [c for c in self._columns(table) if c in row]
        values = [row[c] for c in columns]
        local_id = (
            codec.id_for_uid(table, uid) if codec is not None
            else self.db.id_for_uid(table, uid)
        )
        cur = self.db.connection.cursor()
        try:
            if local_id is None:
                placeholders = ", ".join("?" for _ in columns)
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            else:
                assignments = ", ".join(f"{c} = ?" for c in columns)
                cur.execute(
                    f"UPDATE {table} SET {assignments} WHERE id = ?", [*values, local_id]
                )
        except sqlite3.IntegrityError:
            # Almost always a child arriving before its parent (a session whose
            # subject is still further down the feed). Skipping it keeps the rest
            # of the pull working; the count check at the end of this pass sees
            # the shortfall and re-downloads everything, which fixes it properly.
            logger.warning(
                "Could not apply %s %s yet; a full re-download will settle it",
                api_table, uid, exc_info=True,
            )

    def _delete(self, api_table: str, uid: str, codec=None) -> bool:
        table = sync_policy.TABLE_FOR_API_NAME.get(api_table)
        if table is None:
            return False
        local_id = (
            codec.id_for_uid(table, uid) if codec is not None
            else self.db.id_for_uid(table, uid)
        )
        if local_id is None:
            return False
        self.db.connection.execute(f"DELETE FROM {table} WHERE id = ?", (local_id,))
        return True

    @staticmethod
    def _apply_order(change: dict) -> tuple:
        """Sort key: upserts parent-first, deletes child-first, then by seq.

        ``SYNCED_TABLES`` is already listed parents-first, which is what makes
        this a lookup rather than a graph walk.
        """
        table = sync_policy.TABLE_FOR_API_NAME.get(change.get("table"))
        try:
            rank = sync_policy.SYNCED_TABLES.index(table)
        except ValueError:
            rank = len(sync_policy.SYNCED_TABLES)
        if change.get("op") == "delete":
            # Deletes run after upserts and from the leaves inwards.
            return (1, -rank, int(change.get("seq", 0)))
        return (0, rank, int(change.get("seq", 0)))

    def _columns(self, table: str) -> list[str]:
        cached = self._column_cache.get(table)
        if cached is None:
            cur = self.db.connection.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            cached = [r["name"] for r in cur.fetchall() if r["name"] != "id"]
            self._column_cache[table] = cached
        return cached

    # ── the three phases ────────────────────────────────────────────────
    def push(self, result: SyncResult) -> bool:
        """Send queued operations in order. Stops at the first refusal.

        Returns False when the queue is blocked, so the caller can skip the pull
        and leave the mirror alone until the user resolves it.
        """
        queued = state.pending(self.db.connection)
        if not queued:
            return True

        response = self.client.send_ops(queued, device_id=self.device_id)
        applied = response.get("applied", [])
        for entry in applied:
            match = next(
                (q for q in queued if q["op_id"] == entry.get("op_id")), None
            )
            if match:
                state.drop(self.db.connection, match["id"])
                result.pushed += 1
        return True

    def pull(self, result: SyncResult) -> None:
        cursor = state.last_seq(self.db.connection)
        while True:
            response = self.client.pull(cursor)
            changes = response.get("changes", [])
            # One codec for the whole page: foreign keys resolve from a map
            # loaded once instead of a query per row.
            codec = sync_policy.WireCodec(self.db)

            # Apply parents before children, not in feed order.
            #
            # The feed orders rows by their most recent change, so a goal created
            # early and edited later sorts AFTER a milestone created in between —
            # and inserting that milestone fails, because its goal_id is NOT NULL
            # and the goal is not here yet. Deletes go the other way round, so a
            # parent never disappears out from under a child.
            for change in sorted(changes, key=self._apply_order):
                if change.get("op") == "delete":
                    if self._delete(change["table"], change["uid"], codec):
                        result.deleted += 1
                else:
                    row = change.get("row")
                    if row:
                        self._upsert(change["table"], change["uid"], row, codec)
                        result.applied += 1
            self.db.connection.commit()

            # The server is the only writer of record, so its value wins. A
            # change made here was already queued as an operation and comes back
            # through this same path.
            for key, value in (response.get("settings") or {}).items():
                if key in sync_policy.SYNCED_SETTING_KEYS and value:
                    if self.db.get_setting(key) != value:
                        self.db.set_setting(key, value)
                        result.messages.append(f"{key} is now {value}")

            cursor = int(response.get("seq", cursor))
            state.set_value(self.db.connection, state.LAST_SEQ, cursor)
            if not response.get("more"):
                break

    def verify(self, result: SyncResult, deep: bool = False) -> bool:
        """Compare the mirror with the server. True when they agree.

        Counts catch anything added or lost and are cheap enough to check every
        pass. ``deep`` also compares a hash of every uid, which catches the
        rarer case of the right *number* of the wrong rows.
        """
        report = self.client.integrity(deep=deep)
        self._check_clock(report, result)
        cur = self.db.connection.cursor()
        for api_name, expected in report.get("tables", {}).items():
            table = sync_policy.TABLE_FOR_API_NAME.get(api_name)
            if table is None:
                continue
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            local = int(cur.fetchone()["n"])
            if local != int(expected.get("count", -1)):
                result.messages.append(
                    f"{api_name}: mirror has {local}, server has {expected.get('count')}"
                )
                return False
            wanted_hash = expected.get("hash")
            if wanted_hash and self._uid_hash(table) != wanted_hash:
                result.messages.append(f"{api_name}: same row count, different rows")
                return False
        return True

    def _uid_hash(self, table: str) -> str:
        """The same digest the server computes, over this mirror's uids."""
        cur = self.db.connection.cursor()
        cur.execute(f"SELECT uid FROM {table} ORDER BY uid")
        uids = [row["uid"] or "" for row in cur.fetchall()]
        return hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest()

    @staticmethod
    def _check_clock(report: dict, result: SyncResult) -> None:
        """Complain if the server's wall clock is not ours.

        JobTracker stores naive local times. If the server sits in another
        timezone, a session started from the phone is filed hours off and nothing
        else would ever notice — the rows look perfectly valid.
        """
        stamp = report.get("server_time")
        if not stamp:
            return
        server_now = timeutils.parse_iso(stamp)
        if server_now is None:
            return
        skew = abs((datetime.now() - server_now).total_seconds())
        if skew > 300:
            message = (
                f"server clock is {round(skew / 60)} min from this machine's; "
                "sessions will be recorded at the wrong time"
            )
            logger.warning("Sync: %s", message)
            result.messages.append(message)

    def full_resync(self, result: SyncResult) -> None:
        """Throw the mirror away and rebuild it from the server.

        Safe precisely because the mirror is not authoritative — but it must not
        run while the outbox still holds work, or unsent local changes would be
        wiped. The caller guarantees that.
        """
        snapshot = self.client.snapshot()
        cur = self.db.connection.cursor()
        # Children first: FKs are ON, so a parent cannot go while a child refers.
        for table in reversed(sync_policy.SYNCED_TABLES):
            cur.execute(f"DELETE FROM {table}")
        codec = sync_policy.WireCodec(self.db)
        for table in sync_policy.SYNCED_TABLES:
            for wire_row in snapshot.get(sync_policy.API_NAMES[table], []):
                uid = wire_row.get("uid")
                if uid:
                    self._upsert(sync_policy.API_NAMES[table], uid, wire_row, codec)
                    result.applied += 1
        for key, value in (snapshot.get("settings") or {}).items():
            if key in sync_policy.SYNCED_SETTING_KEYS:
                self.db.set_setting(key, value)
        self.db.connection.commit()
        state.set_value(
            self.db.connection, state.LAST_SEQ, int(snapshot.get("head", 0))
        )
        result.repaired = True

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
            self.pull(result)
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
