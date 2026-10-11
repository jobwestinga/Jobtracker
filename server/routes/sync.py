"""Incremental feed, integrity and full wire snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from jobtracker.core import sync_policy
from .. import feed
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


@router.get("/sync/pull")
def sync_pull(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=5000, ge=1, le=20000),
    device: str = Depends(require_device),
) -> dict:
    with _lock:
        service = svc()
        changes, cursor = feed.changes_since(service.db, since, limit)
        head = feed.current_seq(service.db.connection)
        # Shared settings are not rows, so they never appear in the change feed.
        # Sending them with every pull is what makes a day-start changed on the
        # phone actually reach the Mac; the set is tiny (currently one key).
        settings = {
            key: service.get_setting(key)
            for key in sorted(sync_policy.SYNCED_SETTING_KEYS)
            if service.get_setting(key)
        }
    return {
        "changes": changes,
        "seq": cursor,
        "head": head,
        "more": cursor < head,
        "settings": settings,
    }

@router.get("/sync/integrity")
def sync_integrity(
    deep: bool = Query(default=False),
    device: str = Depends(require_device),
) -> dict:
    """Proof that a client is in step. Row counts always; hashes on request.

    A mismatch means the mirror drifted; the client's answer is to throw the
    mirror away and re-download, which is always safe because the mirror is never
    authoritative.

    Counts alone catch anything added or lost, cost one COUNT(*) per table, and
    so can be checked on every sync. Hashing every uid catches the rarer case of
    the right number of wrong rows, and is reserved for an occasional deep check.
    """
    with _lock:
        service = svc()
        cur = service.db.connection.cursor()
        tables: dict[str, dict] = {}
        for table in sync_policy.SYNCED_TABLES:
            if deep:
                cur.execute(f"SELECT uid FROM {table} ORDER BY uid")
                uids = [row["uid"] or "" for row in cur.fetchall()]
                tables[sync_policy.API_NAMES[table]] = {
                    "count": len(uids),
                    "hash": hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest(),
                }
            else:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                tables[sync_policy.API_NAMES[table]] = {
                    "count": int(cur.fetchone()["n"])
                }
        head = feed.current_seq(service.db.connection)
    # The wall clock rides along so a client can notice the server drifting
    # into another timezone: times are stored naive, so a mismatch silently
    # files work at the wrong hour.
    return {
        "tables": tables,
        "head": head,
        "deep": deep,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }

def _raw_rows(codec, table: str, where: str = "", params: tuple = ()) -> list[dict]:
    """Rows in wire form. The codec resolves foreign keys from a preloaded map,
    so this is one query for the rows plus one per referenced table — not one
    per row, which is what it used to be."""
    cur = codec.db.connection.cursor()
    cur.execute(f"SELECT * FROM {table} {where}", params)
    return [codec.to_wire(table, dict(r)) for r in cur.fetchall()]

@router.get("/api/snapshot")
def api_snapshot(
    include_weights: bool = Query(default=True),
    device: str = Depends(require_device),
) -> dict:
    """Everything a fresh client needs, in one call.

    This is the "throw the mirror away and start over" path, and also what the
    phone loads on a cold start.
    """
    with _lock:
        service = svc()
        service._adopt_open_session(include_other_devices=True)
        db = service.db
        codec = sync_policy.WireCodec(db)
        payload = {
            sync_policy.API_NAMES[table]: _raw_rows(codec, table)
            for table in sync_policy.SYNCED_TABLES
            if include_weights or table != "weight_measurements"
        }
        payload["settings"] = {
            key: db.get_setting(key)
            for key in sorted(sync_policy.SYNCED_SETTING_KEYS)
            if db.get_setting(key)
        }
        payload["head"] = feed.current_seq(db.connection)
        active = service.active_session
        payload["active_session"] = (
            codec.to_wire(
                "sessions",
                dict(
                    db.connection.execute(
                        "SELECT * FROM sessions WHERE id = ?", (active.id,)
                    ).fetchone()
                ),
            )
            if active is not None and active.id is not None
            else None
        )
    return payload
