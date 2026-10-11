"""Timer operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from datetime import datetime
from jobtracker.core import timeutils
from .common import (
    _require_uid, _target_uid, _parse_dt, _adopt_uid, _row, _model_row,
)

def start_subject(svc, p):
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

def stop_active_subject(svc, p):
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

def switch_subject(svc, p):
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

def heartbeat(svc, p):
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

def resolve_recovery(svc, p):
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
