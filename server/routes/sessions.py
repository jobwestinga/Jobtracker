"""Logical-day session reads."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from jobtracker.core import sync_policy
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


@router.get("/api/sessions/day/{day}")
def api_sessions_for_day(day: str, device: str = Depends(require_device)) -> dict:
    try:
        logical = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    with _lock:
        service = svc()
        service._adopt_open_session(include_other_devices=True)
        codec = sync_policy.WireCodec(service.db)
        rows = service.get_sessions_for_logical_day(logical)
        out = []
        for row in rows:
            out.append(
                {
                    # None for the live session, which has no persisted row yet —
                    # the desktop treats that the same way (never editable).
                    "uid": codec.uid_for_id("sessions", row.get("session_id")),
                    "subject_uid": codec.uid_for_id("tasks", row.get("subject_id")),
                    "subject_name": row.get("subject_name"),
                    "color": row.get("color"),
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "duration_seconds": row.get("duration_seconds"),
                }
            )
    return {"day": day, "sessions": out}
