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
import logging
from datetime import datetime
from typing import Any, Callable

from jobtracker.core import sync_policy, timeutils

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


def _require_uid(db, table: str, uid: str, label: str) -> int:
    row_id = db.id_for_uid(table, uid)
    if row_id is None:
        raise OpError(f"unknown {label}: {uid}", status=404)
    return row_id


def _parse_dt(value: Any, label: str) -> datetime:
    parsed = timeutils.parse_iso(value) if isinstance(value, str) else None
    if parsed is None:
        raise OpError(f"{label} must be an ISO-8601 datetime, got {value!r}")
    return parsed


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
    return {"subject": _model_row(svc.db, "tasks", subject)}


@op("update_subject")
def _update_subject(svc, p):
    sid = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    subject = svc.update_subject(sid, p["name"], p["color"], p.get("notes", ""))
    if subject is None:
        raise OpError("subject could not be updated (duplicate or empty name)")
    return {"subject": _model_row(svc.db, "tasks", subject)}


@op("archive_subject")
def _archive_subject(svc, p):
    svc.archive_subject(_require_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("unarchive_subject")
def _unarchive_subject(svc, p):
    svc.unarchive_subject(_require_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("delete_subject")
def _delete_subject(svc, p):
    svc.delete_subject(_require_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}


@op("set_subject_order")
def _set_subject_order(svc, p):
    ids = [_require_uid(svc.db, "tasks", u, "subject") for u in p["subject_uids"]]
    svc.set_subject_order(ids, archived=bool(p.get("archived", False)))
    return {"ok": True}


# ── the running timer ───────────────────────────────────────────────────


@op("start_subject")
def _start_subject(svc, p):
    sid = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    if not svc.start_subject(sid):
        raise OpError("could not start: a session is already running", status=409)
    return {"session": _model_row(svc.db, "sessions", svc.active_session)}


@op("stop_active_subject")
def _stop_active_subject(svc, p):
    if svc.active_session is None:
        # Not an error: two devices may both send a stop for the same timer, and
        # the second one should be a no-op rather than a failure.
        return {"stopped": False}
    session_id = svc.active_session.id
    end_time = _parse_dt(p["end_time"], "end_time") if p.get("end_time") else None
    svc.stop_active_subject(end_time)
    return {"stopped": True, "session": _row(svc.db, "sessions", session_id)}


@op("switch_subject")
def _switch_subject(svc, p):
    sid = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    if not svc.switch_subject(sid):
        raise OpError("could not switch to that subject", status=409)
    return {"session": _model_row(svc.db, "sessions", svc.active_session)}


@op("heartbeat")
def _heartbeat(svc, p):
    svc.heartbeat_active_session()
    return {"ok": True}


# ── sessions ────────────────────────────────────────────────────────────


@op("add_session")
def _add_session(svc, p):
    sid = _require_uid(svc.db, "tasks", p["subject_uid"], "subject")
    session = svc.add_session(
        sid, _parse_dt(p["start_time"], "start_time"), _parse_dt(p["end_time"], "end_time")
    )
    if session is None:
        raise OpError("session could not be created")
    return {"session": _model_row(svc.db, "sessions", session)}


@op("update_session")
def _update_session(svc, p):
    session_id = _require_uid(svc.db, "sessions", p["session_uid"], "session")
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
    svc.delete_session(_require_uid(svc.db, "sessions", p["session_uid"], "session"))
    return {"ok": True}


@op("duplicate_session")
def _duplicate_session(svc, p):
    session_id = _require_uid(svc.db, "sessions", p["session_uid"], "session")
    to = p.get("to", "today")
    if to not in {"today", "next_day"}:
        raise OpError("to must be 'today' or 'next_day'")
    session = svc.duplicate_session(session_id, to=to)
    if session is None:
        raise OpError("session could not be duplicated")
    return {"session": _model_row(svc.db, "sessions", session)}


@op("shift_session")
def _shift_session(svc, p):
    session_id = _require_uid(svc.db, "sessions", p["session_uid"], "session")
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
    return {"goal": _model_row(svc.db, "todo_tasks", goal)}


@op("update_goal")
def _update_goal(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    goal = svc.update_todo_task(gid, p["name"], p.get("notes", ""), p.get("deadline"))
    if goal is None:
        raise OpError("goal could not be updated")
    return {"goal": _model_row(svc.db, "todo_tasks", goal)}


@op("delete_goal")
def _delete_goal(svc, p):
    svc.delete_todo_task(_require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal"))
    return {"ok": True}


@op("complete_goal")
def _complete_goal(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    if not svc.complete_goal(gid):
        # The milestone gate is a real rule, not a race: report it as a refusal.
        raise OpError("goal has unchecked milestones", status=409)
    return {"goal": _row(svc.db, "todo_tasks", gid)}


@op("uncomplete_goal")
def _uncomplete_goal(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    svc.uncomplete_goal(gid)
    return {"goal": _row(svc.db, "todo_tasks", gid)}


@op("toggle_goal_focused")
def _toggle_goal_focused(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    focused = svc.toggle_goal_focused(gid)
    return {"focused": focused, "goal": _row(svc.db, "todo_tasks", gid)}


@op("set_goal_order")
def _set_goal_order(svc, p):
    ids = [_require_uid(svc.db, "todo_tasks", u, "goal") for u in p["goal_uids"]]
    svc.set_todo_task_order(ids, completed=bool(p.get("completed", False)))
    return {"ok": True}


# ── milestones ──────────────────────────────────────────────────────────


@op("add_milestone")
def _add_milestone(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    milestone = svc.add_milestone(gid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be created (empty title?)")
    return {"milestone": _model_row(svc.db, "milestones", milestone)}


@op("update_milestone")
def _update_milestone(svc, p):
    mid = _require_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    milestone = svc.update_milestone(mid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be updated")
    return {"milestone": _model_row(svc.db, "milestones", milestone)}


@op("set_milestone_done")
def _set_milestone_done(svc, p):
    mid = _require_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    svc.set_milestone_done(mid, bool(p["done"]))
    return {"milestone": _row(svc.db, "milestones", mid)}


@op("delete_milestone")
def _delete_milestone(svc, p):
    svc.delete_milestone(
        _require_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    )
    return {"ok": True}


@op("set_milestone_order")
def _set_milestone_order(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    ids = [_require_uid(svc.db, "milestones", u, "milestone") for u in p["milestone_uids"]]
    svc.set_milestone_order(gid, ids)
    return {"ok": True}


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


def apply_op(svc, op_name: str, params: dict, op_id: str, device_id: str | None) -> dict:
    """Run one operation, exactly once.

    A repeated ``op_id`` short-circuits to the recorded response, so a client that
    retries after a timeout gets the original result rather than a duplicate.
    """
    cur = svc.db.connection.cursor()
    cur.execute("SELECT response FROM applied_ops WHERE op_id = ?", (op_id,))
    seen = cur.fetchone()
    if seen:
        logger.info("op %s (%s) already applied; replaying result", op_id, op_name)
        return {**json.loads(seen["response"]), "replayed": True}

    handler = _HANDLERS.get(op_name)
    if handler is None:
        raise OpError(f"unknown operation: {op_name}", status=400)

    result = handler(svc, params or {})

    cur.execute(
        "INSERT INTO applied_ops (op_id, op_name, device_id, response) "
        "VALUES (?, ?, ?, ?)",
        (op_id, op_name, device_id, json.dumps(result, default=str)),
    )
    svc.db.connection.commit()
    return {**result, "replayed": False}
