"""Operation errors, recorded skips and UID/wire translation helpers.

Creates require their parents; edits of removed targets become recorded skips.
The central dispatcher owns transactions and receipts for either result.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from jobtracker.core import sync_policy, timeutils

logger = logging.getLogger("jobtracker.server")


class OpError(Exception):
    """An operation could not be applied. Carries an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

class Skipped(Exception):
    """The row this operation targets is gone. Not an error — see below."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

def _require_uid(db, table: str, uid: str, label: str) -> int:
    """Resolve a uid that MUST exist. Only for creates.

    A create whose parent is missing has to fail: quietly dropping a session
    would lose the work it records.
    """
    row_id = db.id_for_uid(table, uid)
    if row_id is None:
        raise OpError(f"unknown {label}: {uid}", status=404)
    return row_id

def _target_uid(db, table: str, uid: str, label: str) -> int:
    """Resolve a uid for an edit or a delete, or skip the operation.

    Deliberately NOT an error when the row is gone. The outbox stops at the first
    refusal — that is what keeps a delete from overtaking its own create — so an
    operation that can never succeed would block every later write forever.

    That is not hypothetical: delete a session on the phone while offline, delete
    the same one on the Mac, reconnect, and the phone's queue would jam for good.
    The row is already gone, which is what the operation wanted, so there is
    nothing to do and nothing to lose.
    """
    row_id = db.id_for_uid(table, uid)
    if row_id is None:
        raise Skipped(f"{label} {uid} no longer exists")
    return row_id

def _existing_uids(db, table: str, uids, label: str) -> list[int]:
    """Resolve a list for a reorder, dropping any that have gone.

    A reorder naming one deleted row is still a perfectly good instruction for
    the rest of them.
    """
    resolved = []
    for uid in uids or []:
        row_id = db.id_for_uid(table, uid)
        if row_id is None:
            logger.info("Reorder skipped missing %s %s", label, uid)
            continue
        resolved.append(row_id)
    return resolved

def _parse_dt(value: Any, label: str) -> datetime:
    parsed = timeutils.parse_iso(value) if isinstance(value, str) else None
    if parsed is None:
        raise OpError(f"{label} must be an ISO-8601 datetime, got {value!r}")
    return parsed

def _adopt_uid(db, table: str, row_id: int, wanted: str | None) -> None:
    """Give a freshly created row the uid the client already assigned it.

    A client that creates a row while offline mints the uid locally and shows it
    immediately. When the operation finally reaches the server, the row must end
    up with *that* uid — otherwise the server would invent a second identity for
    the same thing and the client would pull it back as a duplicate.

    A uid already belonging to a different row is a conflict. The surrounding
    transaction rolls back the newly created row instead of duplicating it.
    """
    if wanted is None:
        return
    if not isinstance(wanted, str):
        raise OpError("row identity must be a UUID string")
    try:
        uuid.UUID(wanted)
    except (ValueError, AttributeError) as exc:
        raise OpError("row identity must be a UUID string") from exc
    if row_id is None:
        return
    existing = db.id_for_uid(table, wanted)
    if existing is not None and int(existing) != int(row_id):
        raise OpError("row identity already belongs to an existing row", status=409)
    db.connection.execute(
        f"UPDATE {table} SET uid = ? WHERE id = ?", (wanted, int(row_id))
    )
    db.connection.commit()

def _row(db, table: str, row_id: int) -> dict | None:
    if row_id is None:
        return None
    cur = db.connection.cursor()
    cur.execute(f"SELECT * FROM {table} WHERE id = ?", (int(row_id),))
    row = cur.fetchone()
    return sync_policy.to_wire(db, table, dict(row)) if row else None

def _model_row(db, table: str, model) -> dict | None:
    return _row(db, table, getattr(model, "id", None)) if model else None
