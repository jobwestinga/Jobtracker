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
import uuid
from datetime import datetime
from typing import Any, Callable

from jobtracker.core import sync_policy, timeutils
from jobtracker.core.backup_validation import validate_backup

logger = logging.getLogger("jobtracker.server")

_HANDLERS: dict[str, Callable] = {}


class OpError(Exception):
    """An operation could not be applied. Carries an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def op(name: str):
    def register(fn: Callable) -> Callable:
        _HANDLERS[name] = fn
        return fn

    return register


def known_ops() -> list[str]:
    return sorted(_HANDLERS)


# ── helpers ─────────────────────────────────────────────────────────────


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


# ── subjects ────────────────────────────────────────────────────────────


@op("add_subject")
def _add_subject(svc, p):
    subject = svc.add_subject(p["name"], p.get("color", "#3B82F6"), p.get("notes", ""))
    if subject is None:
        raise OpError("subject could not be created (duplicate or empty name)")
    _adopt_uid(svc.db, "tasks", subject.id, p.get("uid"))
    return {"subject": _row(svc.db, "tasks", subject.id)}


@op("update_subject")
def _update_subject(svc, p):
    sid = _target_uid(svc.db, "tasks", p["subject_uid"], "subject")
    subject = svc.update_subject(sid, p["name"], p["color"], p.get("notes", ""))
    if subject is None:
        raise OpError("subject could not be updated (duplicate or empty name)")
    return {"subject": _model_row(svc.db, "tasks", subject)}


@op("archive_subject")
def _archive_subject(svc, p):
    svc.archive_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("unarchive_subject")
def _unarchive_subject(svc, p):
    svc.unarchive_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("delete_subject")
def _delete_subject(svc, p):
    svc.delete_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("set_subject_order")
def _set_subject_order(svc, p):
    ids = _existing_uids(svc.db, "tasks", p["subject_uids"], "subject")
    svc.set_subject_order(ids, archived=bool(p.get("archived", False)))
    return {"ok": True}


# ── the running timer ───────────────────────────────────────────────────


@op("start_subject")
def _start_subject(svc, p):
    wanted = p.get("uid")
    # A captured offline timer is a create: skipping it after its parent was
    # deleted would acknowledge and erase recorded work on the client.
    resolver = _require_uid if wanted and "start_time" in p else _target_uid
    sid = resolver(svc.db, "tasks", p["subject_uid"], "subject")
    existing_id = svc.db.id_for_uid("sessions", wanted) if wanted else None
    if existing_id is not None:
        return {"started": False, "session": _row(svc.db, "sessions", existing_id),
                "reason": "this session is already recorded"}
    start_time = _parse_dt(p["start_time"], "start_time") if "start_time" in p else None
    if not svc.start_subject(sid, start_time, allow_parallel=bool(wanted and start_time)):
        # Something is already running. Retrying will not change that, so report
        # it rather than jamming everything queued behind it.
        return {"started": False, "reason": "a session is already running"}
    _adopt_uid(svc.db, "sessions", svc.active_session.id, p.get("uid"))
    return {"started": True, "session": _row(svc.db, "sessions", svc.active_session.id)}


@op("stop_active_subject")
def _stop_active_subject(svc, p):
    session = _timer_target(svc, p)
    if session is None or session.end_time is not None:
        # Not an error: two devices may both send a stop for the same timer, and
        # the second one should be a no-op rather than a failure.
        return {"stopped": False}
    session_id = session.id
    end_time = _parse_dt(p["end_time"], "end_time") if "end_time" in p else None
    svc.active_session = session
    svc.active_subject = svc.db.get_subject(session.subject_id)
    svc.stop_active_subject(end_time)
    row = _row(svc.db, "sessions", session_id)
    stopped = row is None or row.get("end_time") is not None
    svc._adopt_open_session(include_other_devices=True)
    return {"stopped": stopped, "session": row,
            **({"reason": "end time precedes session start"} if not stopped else {})}


@op("switch_subject")
def _switch_subject(svc, p):
    wanted = p.get("uid")
    recorded = bool(wanted and any(key in p for key in ("moment", "start_time", "end_time")))
    resolver = _require_uid if recorded else _target_uid
    sid = resolver(svc.db, "tasks", p["subject_uid"], "subject")
    existing_id = svc.db.id_for_uid("sessions", wanted) if wanted else None
    if existing_id is not None:
        return {"switched": False, "session": _row(svc.db, "sessions", existing_id),
                "reason": "this session is already recorded"}
    moment_key = next((key for key in ("moment", "start_time", "end_time") if key in p), None)
    moment_value = p[moment_key] if moment_key else None
    moment = _parse_dt(moment_value, "moment") if moment_key else datetime.now()
    # Parse every timestamp before closing the previous timer. An invalid new
    # start must never leave a switch half-applied.
    end_time = _parse_dt(p["end_time"], "end_time") if "end_time" in p else moment
    source = None
    if "source_session_uid" in p:
        source_id = svc.db.id_for_uid("sessions", p["source_session_uid"])
        source = svc.db.get_session(source_id) if source_id is not None else None
    else:
        source = svc.active_session
        if source is not None and source.subject_id == sid:
            return {"switched": False, "reason": "already tracking that subject"}
    subject = svc.db.get_subject(sid)
    if subject is None or (subject.is_archived and not recorded):
        return {"switched": False, "reason": "subject is archived or missing"}
    if source is not None and source.end_time is None:
        start = timeutils.parse_iso(source.start_time)
        if start is None or end_time < start:
            return {"switched": False, "reason": "end time precedes session start"}
        svc.active_session = source
        svc.active_subject = svc.db.get_subject(source.subject_id)
        svc.stop_active_subject(end_time)
    if not svc.start_subject(sid, moment, allow_parallel=bool(wanted and moment_value)):
        return {"switched": False, "reason": "a session is already running"}
    _adopt_uid(svc.db, "sessions", svc.active_session.id, wanted)
    return {
        "switched": True,
        "session": _model_row(svc.db, "sessions", svc.active_session),
    }


@op("heartbeat")
def _heartbeat(svc, p):
    session = _timer_target(svc, p)
    if session is None or session.end_time is not None:
        return {"ok": True}
    svc.active_session = session
    svc.active_subject = svc.db.get_subject(session.subject_id)
    moment = _parse_dt(p["moment"], "moment") if "moment" in p else None
    svc.heartbeat_active_session(moment)
    svc._adopt_open_session(include_other_devices=True)
    return {"ok": True}


def _timer_target(svc, p):
    """Target the timer originally tapped, even after a different one starts."""
    if "session_uid" in p:
        session_id = svc.db.id_for_uid("sessions", p["session_uid"])
        return svc.db.get_session(session_id) if session_id is not None else None
    return svc.active_session


@op("resolve_recovery")
def _resolve_recovery(svc, p):
    session = _timer_target(svc, p)
    if session is None or session.end_time is not None:
        return {"resolved": False}
    end_time = _parse_dt(p["end_time"], "end_time")
    svc.active_session = session
    svc.active_subject = svc.db.get_subject(session.subject_id)
    svc.resolve_recovery(end_time, session_id=session.id)
    row = _row(svc.db, "sessions", session.id)
    svc._adopt_open_session(include_other_devices=True)
    return {"resolved": bool(row and row.get("end_time")), "session": row}


# ── sessions ────────────────────────────────────────────────────────────


@op("add_session")
def _add_session(svc, p):
    # Strict: a session whose subject is missing has nowhere to go, and dropping
    # it silently would lose recorded work.
    sid = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    session = svc.add_session(
        sid, _parse_dt(p["start_time"], "start_time"), _parse_dt(p["end_time"], "end_time")
    )
    if session is None:
        raise OpError("session could not be created")
    _adopt_uid(svc.db, "sessions", session.id, p.get("uid"))
    return {"session": _row(svc.db, "sessions", session.id)}


@op("update_session")
def _update_session(svc, p):
    session_id = _target_uid(svc.db, "sessions", p["session_uid"], "session")
    subject_id = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    session = svc.update_session(
        session_id,
        subject_id,
        _parse_dt(p["start_time"], "start_time"),
        _parse_dt(p["end_time"], "end_time"),
    )
    if session is None:
        raise OpError("session could not be updated")
    return {"session": _model_row(svc.db, "sessions", session)}


@op("delete_session")
def _delete_session(svc, p):
    svc.delete_session(_target_uid(svc.db, "sessions", p["session_uid"], "session"))
    return {"ok": True}


@op("duplicate_session")
def _duplicate_session(svc, p):
    session_id = _target_uid(svc.db, "sessions", p["session_uid"], "session")
    to = p.get("to", "today")
    if to not in {"today", "next_day"}:
        raise OpError("to must be 'today' or 'next_day'")
    session = svc.duplicate_session(session_id, to=to)
    if session is None:
        raise OpError("session could not be duplicated")
    _adopt_uid(svc.db, "sessions", session.id, p.get("uid"))
    return {"session": _row(svc.db, "sessions", session.id)}


@op("shift_session")
def _shift_session(svc, p):
    session_id = _target_uid(svc.db, "sessions", p["session_uid"], "session")
    session = svc.shift_session(session_id, int(p["seconds"]))
    if session is None:
        raise OpError("session could not be shifted")
    return {"session": _model_row(svc.db, "sessions", session)}


# ── goals ───────────────────────────────────────────────────────────────


@op("add_goal")
def _add_goal(svc, p):
    goal = svc.add_todo_task(p["name"], p.get("notes", ""), p.get("deadline"))
    if goal is None:
        raise OpError("goal could not be created (empty name?)")
    _adopt_uid(svc.db, "todo_tasks", goal.id, p.get("uid"))
    return {"goal": _row(svc.db, "todo_tasks", goal.id)}


@op("update_goal")
def _update_goal(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    goal = svc.update_todo_task(gid, p["name"], p.get("notes", ""), p.get("deadline"))
    if goal is None:
        raise OpError("goal could not be updated")
    return {"goal": _model_row(svc.db, "todo_tasks", goal)}


@op("delete_goal")
def _delete_goal(svc, p):
    svc.delete_todo_task(_target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal"))
    return {"ok": True}


@op("complete_goal")
def _complete_goal(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    if not svc.complete_goal(gid):
        # The milestone gate is a real rule. Report it in the result instead of
        # failing: the client checked before queueing, so reaching here means the
        # milestones changed elsewhere, and no retry will ever succeed.
        return {
            "completed": False,
            "reason": "goal has unchecked milestones",
            "goal": _row(svc.db, "todo_tasks", gid),
        }
    return {"completed": True, "goal": _row(svc.db, "todo_tasks", gid)}


@op("uncomplete_goal")
def _uncomplete_goal(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    svc.uncomplete_goal(gid)
    return {"goal": _row(svc.db, "todo_tasks", gid)}


@op("toggle_goal_focused")
def _toggle_goal_focused(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    focused = svc.toggle_goal_focused(gid)
    return {"focused": focused, "goal": _row(svc.db, "todo_tasks", gid)}


@op("set_goal_order")
def _set_goal_order(svc, p):
    ids = _existing_uids(svc.db, "todo_tasks", p["goal_uids"], "goal")
    svc.set_todo_task_order(ids, completed=bool(p.get("completed", False)))
    return {"ok": True}


# ── milestones ──────────────────────────────────────────────────────────


@op("add_milestone")
def _add_milestone(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    milestone = svc.add_milestone(gid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be created (empty title?)")
    _adopt_uid(svc.db, "milestones", milestone.id, p.get("uid"))
    return {"milestone": _row(svc.db, "milestones", milestone.id)}


@op("update_milestone")
def _update_milestone(svc, p):
    mid = _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    milestone = svc.update_milestone(mid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be updated")
    return {"milestone": _model_row(svc.db, "milestones", milestone)}


@op("set_milestone_done")
def _set_milestone_done(svc, p):
    mid = _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    svc.set_milestone_done(mid, bool(p["done"]))
    return {"milestone": _row(svc.db, "milestones", mid)}


@op("delete_milestone")
def _delete_milestone(svc, p):
    svc.delete_milestone(
        _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    )
    return {"ok": True}


@op("set_milestone_order")
def _set_milestone_order(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    ids = _existing_uids(svc.db, "milestones", p["milestone_uids"], "milestone")
    svc.set_milestone_order(gid, ids)
    return {"ok": True}


# ── recurring templates ─────────────────────────────────────────────────
#
# Generation runs HERE and only here. If each client generated its own instances
# they would each create a goal for the same period and each set `last_generated`
# locally, so the two machines would disagree about a period that had already
# been produced. One generator, pulled by everyone, keeps it idempotent.


@op("add_template")
def _add_template(svc, p):
    template = svc.add_goal_template(
        p["title"],
        p.get("notes", ""),
        p.get("recurrence", "daily"),
        p.get("milestone_titles") or [],
        p.get("recurrence_day"),
    )
    if template is None:
        raise OpError("template could not be created (empty title?)")
    _adopt_uid(svc.db, "goal_templates", template.id, p.get("uid"))
    return {"template": _row(svc.db, "goal_templates", template.id)}


@op("update_template")
def _update_template(svc, p):
    tid = _target_uid(svc.db, "goal_templates", p["template_uid"], "template")
    template = svc.update_goal_template(
        tid,
        p["title"],
        p.get("notes", ""),
        p.get("recurrence", "daily"),
        p.get("milestone_titles") or [],
        p.get("recurrence_day"),
    )
    if template is None:
        raise OpError("template could not be updated")
    return {"template": _row(svc.db, "goal_templates", tid)}


@op("set_template_active")
def _set_template_active(svc, p):
    tid = _target_uid(svc.db, "goal_templates", p["template_uid"], "template")
    svc.set_goal_template_active(tid, bool(p["active"]))
    return {"template": _row(svc.db, "goal_templates", tid)}


@op("delete_template")
def _delete_template(svc, p):
    svc.delete_goal_template(
        _target_uid(svc.db, "goal_templates", p["template_uid"], "template")
    )
    return {"ok": True}


@op("generate_due_goals")
def _generate_due_goals(svc, p):
    """Create this period's goal instances. Idempotent within a period, so a
    client may call it on every launch without producing duplicates."""
    created = svc.generate_due_goal_instances()
    return {
        "created": [svc.db.uid_for_id("todo_tasks", gid) for gid in created],
    }


# ── settings ────────────────────────────────────────────────────────────


@op("set_setting")
def _set_setting(svc, p):
    key = p["key"]
    if key not in sync_policy.SYNCED_SETTING_KEYS:
        # Theme, graph range and the like are per-machine on purpose: the phone
        # does not get to dictate the Mac's window state.
        raise OpError(f"{key} is a device-local setting and is not shared")
    svc.set_setting(key, str(p["value"]))
    return {"key": key, "value": svc.get_setting(key)}


# ── dispatch ────────────────────────────────────────────────────────────


@op("upsert_weight")
def _upsert_weight(svc, p):
    from jobtracker.services.weight_service import WeightService
    return WeightService(svc.db).upsert(
        p["measured_at"], p["weight_kg"], uid=p.get("uid"), weight_uid=p.get("weight_uid")
    )


@op("delete_weight")
def _delete_weight(svc, p):
    from jobtracker.services.weight_service import WeightService
    return {"deleted": WeightService(svc.db).delete(p["weight_uid"])}


@op("import_weights")
def _import_weights(svc, p):
    from jobtracker.services.weight_service import WeightService
    if not isinstance(p.get("measurements"), list) or len(p["measurements"]) > 5000:
        raise OpError("Import must contain at most 5000 measurements")
    return WeightService(svc.db).import_rows(p["measurements"])


@op("import_backup")
def _import_backup(svc, p):
    data = p["data"]
    validate_backup(data)
    # The existing importer maps legacy parent IDs and merges by contents.
    # A queued mirror export may have stale contents for a known UID: replace
    # those parents with their authoritative rows while retaining the export's
    # IDs for mapping its new children. Known children need no second import.
    merged = dict(data)
    for key, table in (
        ("subjects", "tasks"), ("tasks", "tasks"),
        ("goal_templates", "goal_templates"), ("todo_tasks", "todo_tasks"),
    ):
        if key not in data:
            continue
        canonical = {
            row["uid"]: dict(row)
            for row in svc.db.connection.execute(f"SELECT * FROM {table}")
        }
        rows = []
        for incoming in data[key]:
            existing = canonical.get(incoming.get("uid"))
            rows.append({**existing, "id": incoming.get("id")} if existing else incoming)
        merged[key] = rows
    for table in ("sessions", "milestones"):
        existing = {row["uid"] for row in svc.db.connection.execute(f"SELECT uid FROM {table}")}
        merged[table] = [row for row in data.get(table, []) if row.get("uid") not in existing]
    merged["settings"] = [
        row for row in data.get("settings", [])
        if row.get("key") in sync_policy.SYNCED_SETTING_KEYS
    ]
    svc.import_data(merged)
    return {"imported": True}


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
