"""Server-computed graph reads and plot-window validation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from jobtracker.core import sync_policy, timeutils
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


@router.get("/api/graphs/breakdown")
def api_breakdown(
    grouping: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(default=14, ge=1, le=3660),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    all_time: bool = Query(default=False),
    device: str = Depends(require_device),
) -> dict:
    """Server-computed totals — the phone draws, it never recalculates.

    That is what stops the phone's numbers from disagreeing with the Mac's.
    """
    with _lock:
        service = svc()
        service._adopt_open_session(include_other_devices=True)
        codec = sync_policy.WireCodec(service.db)
        start, end = _plot_window(start_date, end_date)
        if all_time:
            day_start = service.get_day_start()
            start = timeutils.logical_day(datetime.now(), day_start)
            for row in service.db.connection.execute("SELECT start_time FROM sessions ORDER BY start_time"):
                earliest = timeutils.parse_iso(row["start_time"])
                if earliest is not None:
                    start = timeutils.logical_day(earliest, day_start)
                    break
            end = service.graph_end_day(day_start)
        buckets = service.get_subject_breakdown(
            grouping=grouping, days=days, start_date=start, end_date=end
        )
        out_buckets = []
        for bucket in buckets:
            out_buckets.append(
                {
                    "date": bucket["date"],
                    "total_seconds": bucket["total_seconds"],
                    "intensity_seconds": bucket["intensity_seconds"],
                    "segments": [
                        {
                            "subject_uid": codec.uid_for_id("tasks", seg["subject_id"]),
                            "subject_name": seg["subject_name"],
                            "color": seg["color"],
                            "seconds": seg["seconds"],
                            "start_time": seg.get("start_time"),
                            "end_time": seg.get("end_time"),
                        }
                        for seg in bucket["segments"]
                    ],
                }
            )
    return {"grouping": grouping, "buckets": out_buckets}

@router.get("/api/graphs/agenda")
def api_agenda(
    days: int = Query(default=7, ge=1, le=60),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    device: str = Depends(require_device),
) -> dict:
    """The agenda timeline: sessions placed at their clock position.

    ``start_h``/``end_h`` are agenda hours within the logical day — after-midnight
    work maps to 24..27 so it renders at the bottom of the day it belongs to
    rather than padding the top of the next one. The phone only draws these.
    """
    with _lock:
        service = svc()
        service._adopt_open_session(include_other_devices=True)
        day_start = service.get_day_start()
        end_day = service.graph_end_day(day_start)
        start_day = timeutils.logical_day(datetime.now(), day_start) - timedelta(
            days=days - 1
        )
        explicit_start, explicit_end = _plot_window(start_date, end_date)
        start_day = explicit_start or start_day
        end_day = explicit_end or end_day
        if start_day > end_day:
            raise HTTPException(status_code=400, detail="Plot end date must be on or after the start date")
        if explicit_start and explicit_end and (end_day - start_day).days >= 60:
            raise HTTPException(status_code=400, detail="Agenda supports up to 60 days. Choose a shorter range.")
        codec = sync_policy.WireCodec(service.db)
        day_keys, sessions = service.get_agenda_data(start_day, end_day, day_start)
        out = [
            {
                "uid": codec.uid_for_id("sessions", s.get("session_id")),
                "day": s["day"],
                "subject_uid": codec.uid_for_id("tasks", s.get("subject_id")),
                "subject_name": s["subject_name"],
                "color": s["color"],
                "start_h": s["start_h"],
                "end_h": s["end_h"],
                "duration_seconds": s["duration_seconds"],
            }
            for s in sessions
        ]
    return {
        "days": day_keys,
        "sessions": out,
        "day_start_hour": day_start.hour + day_start.minute / 60.0,
    }

def _plot_window(start, end):
    try:
        start = date.fromisoformat(start) if start else None
        end = date.fromisoformat(end) if end else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Plot dates must be YYYY-MM-DD") from None
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="Plot end date must be on or after the start date")
    return start, end
