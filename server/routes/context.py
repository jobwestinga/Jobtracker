"""Server context and active timer reads."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from jobtracker.core import sync_policy, timeutils
from .. import feed
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


@router.get("/api/active")
def api_active(device: str = Depends(require_device)) -> dict:
    """The running timer, as any device sees it.

    Re-read from the database rather than trusting in-memory state, so a timer
    started by another client is visible immediately.
    """
    with _lock:
        service = svc()
        service._adopt_open_session(include_other_devices=True)
        active = service.active_session
        if active is None or active.id is None:
            return {"active": None}
        row = service.db.connection.execute(
            "SELECT * FROM sessions WHERE id = ?", (active.id,)
        ).fetchone()
        subject_uid = service.db.uid_for_id("tasks", active.subject_id)
        elapsed = timeutils.duration_seconds(
            timeutils.parse_iso(active.start_time), datetime.now()
        )
        active_wire = sync_policy.to_wire(service.db, "sessions", dict(row))
    return {
        "active": active_wire,
        "subject_uid": subject_uid,
        "elapsed_seconds": elapsed,
    }

@router.get("/api/context")
def api_context(device: str = Depends(require_device)) -> dict:
    """Today, as the server reckons it.

    The logical day starts at 03:00 by default, so "today" is not simply the
    calendar date. The phone asks rather than working it out, because a second
    implementation of that rule is a second thing that can disagree with the Mac.
    """
    with _lock:
        service = svc()
        day_start = service.get_day_start()
        today = timeutils.logical_day(datetime.now(), day_start)
        # `head` lets a client ask "has anything changed?" for a few dozen bytes
        # instead of re-downloading a snapshot that is several hundred kilobytes.
        head = feed.current_seq(service.db.connection)
        service._adopt_open_session(include_other_devices=True)
        active = service.active_session
        active_payload = None
        if active is not None and active.id is not None:
            active_payload = {
                "session_uid": active.uid,
                "uid": active.uid,
                "subject_uid": service.db.uid_for_id("tasks", active.subject_id),
                "start_time": active.start_time,
                "elapsed_seconds": timeutils.duration_seconds(
                    timeutils.parse_iso(active.start_time), datetime.now()
                ),
            }
        return {
            "today": today.isoformat(),
            "day_start": timeutils.day_start_to_str(day_start),
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "head": head,
            "active": active_payload,
        }
