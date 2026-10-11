"""Operations: the only way anything is written.

Each operation is named after the ``TrackerService`` method it performs, takes
**uids** rather than local ids, and runs through the real service — so the
sub-30-second stop rule, milestone-gated completion, the switch-subject rules and
template generation all execute in exactly one place, no matter whether the
request came from the Mac app or the phone.

Two properties this module exists to guarantee:

* **Idempotency.** Every request carries a client-generated ``op_id``. The result
  is recorded against that id, and a repeat returns the stored result instead of
  running again. A retry after a dropped connection therefore cannot create a
  second session — the classic offline-queue bug, removed by construction.
* **No integer ids on the wire.** Arguments arrive as uids and are resolved here;
  results are converted back with ``sync_policy.to_wire``.
"""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
from typing import Callable

from .operations import register_operations
from .operations.common import OpError, Skipped

logger = logging.getLogger("jobtracker.server")

_HANDLERS: dict[str, Callable] = {}


def op(name: str):
    def register(fn: Callable) -> Callable:
        _HANDLERS[name] = fn
        return fn

    return register


def known_ops() -> list[str]:
    return sorted(_HANDLERS)


# Explicit domain registration retains one runtime registry, also used by tests
# that inject failure handlers to verify atomic mutation/receipt behavior.
register_operations(op)


def apply_op(
    svc, op_name: str, params: dict, op_id: str, device_id: str | None,
    *, request_device: str | None = None,
) -> dict:
    """Run one operation, exactly once.

    A repeated ``op_id`` short-circuits to the recorded response, so a client that
    retries after a timeout gets the original result rather than a duplicate.
    """
    cur = svc.db.connection.cursor()
    try:
        request_hash = hashlib.sha256(json.dumps(
            {"op": op_name, "params": params or {}, "device_id": device_id,
             "principal": request_device or device_id},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise OpError("operation parameters must be valid JSON") from exc
    cur.execute(
        "SELECT response, op_name, device_id, request_hash FROM applied_ops WHERE op_id = ?", (op_id,)
    )
    seen = cur.fetchone()
    if seen:
        if seen["op_name"] != op_name or seen["device_id"] != device_id or (
            seen["request_hash"] is not None and seen["request_hash"] != request_hash
        ):
            raise OpError("op_id already belongs to a different request", status=409)
        logger.info("op %s (%s) already applied; replaying result", op_id, op_name)
        return {**json.loads(seen["response"]), "replayed": True}

    handler = _HANDLERS.get(op_name)
    if handler is None:
        raise OpError(f"unknown operation: {op_name}", status=400)

    original_device = svc.device_id
    try:
        # Persist the write and its retry receipt together. Otherwise a crash
        # after the row commits but before its receipt can repeat the mutation.
        with svc.db.atomic():
            svc._adopt_open_session(include_other_devices=True)
            svc.device_id = device_id or original_device
            try:
                for field in ("name", "title", "color", "notes", "note", "recurrence"):
                    if field in (params or {}) and params[field] is not None and not isinstance(params[field], str):
                        raise ValueError(f"{field} must be text")
                result = handler(svc, params or {})
            except Skipped as skip:
                logger.info("op %s skipped: %s", op_name, skip.reason)
                result = {"skipped": True, "reason": skip.reason}
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError,
                    sqlite3.ProgrammingError, sqlite3.IntegrityError) as exc:
                raise OpError(f"invalid parameters for {op_name}: {exc}") from exc
            cur.execute(
                "INSERT INTO applied_ops (op_id, op_name, device_id, response, request_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (op_id, op_name, device_id, json.dumps(result, default=str), request_hash),
            )
    finally:
        svc.device_id = original_device
        svc._adopt_open_session(include_other_devices=True)
    return {**result, "replayed": False}
