"""Sessions operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError, _require_uid, _target_uid, _parse_dt, _adopt_uid, _row, _model_row,
)

def add_session(svc, p):
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

def update_session(svc, p):
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

def delete_session(svc, p):
    svc.delete_session(_target_uid(svc.db, "sessions", p["session_uid"], "session"))
    return {"ok": True}

def duplicate_session(svc, p):
    session_id = _target_uid(svc.db, "sessions", p["session_uid"], "session")
    to = p.get("to", "today")
    if to not in {"today", "next_day"}:
        raise OpError("to must be 'today' or 'next_day'")
    session = svc.duplicate_session(session_id, to=to)
    if session is None:
        raise OpError("session could not be duplicated")
    _adopt_uid(svc.db, "sessions", session.id, p.get("uid"))
    return {"session": _row(svc.db, "sessions", session.id)}

def shift_session(svc, p):
    session_id = _target_uid(svc.db, "sessions", p["session_uid"], "session")
    session = svc.shift_session(session_id, int(p["seconds"]))
    if session is None:
        raise OpError("session could not be shifted")
    return {"session": _model_row(svc.db, "sessions", session)}
