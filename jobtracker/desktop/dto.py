"""Typed, UID-based data shapes sent to the native client.

Local integer IDs never cross the boundary; rows use ``sync_policy.to_wire`` so
the shapes match the server/phone reads. Timestamps are the exact stored naive
local strings. Calculations (durations, logical days, intensity) come from the
existing services so the native app never re-implements a rule.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ..core import sync_policy, timeutils


def subject(db, row, total_seconds: Optional[int] = None) -> dict:
    return {
        "uid": row.uid,
        "name": row.name,
        "color": row.color,
        "notes": row.notes or "",
        "sort_order": row.sort_order,
        "archived": bool(row.is_archived),
        "created_at": row.created_at,
        "total_seconds": total_seconds,
    }


def session(db, row, *, device_id: str, subjects_by_id: Optional[dict] = None) -> dict:
    subject_row = (subjects_by_id or {}).get(row.task_id) if subjects_by_id is not None else db.get_subject(row.task_id)
    return {
        "uid": row.uid,
        "subject_uid": subject_row.uid if subject_row else db.uid_for_id("tasks", row.task_id),
        "subject_name": subject_row.name if subject_row else "",
        "color": subject_row.color if subject_row else "",
        "start_time": row.start_time,
        "end_time": row.end_time,
        "duration_seconds": int(row.duration_seconds or 0),
        "open": row.end_time is None,
        "owned": row.device_id is None or row.device_id == device_id,
    }


def milestone(row, goal_uid: str) -> dict:
    return {
        "uid": row.uid,
        "goal_uid": goal_uid,
        "title": row.title,
        "note": row.note or "",
        "done": bool(row.is_done),
        "sort_order": row.sort_order,
    }


def goal(service, row) -> dict:
    milestones = service.get_goal_milestones(row.id)
    done, total = service.get_goal_progress(row.id)
    return {
        "uid": row.uid,
        "name": row.name,
        "notes": row.notes or "",
        "deadline": row.deadline,
        "completed": bool(row.is_completed),
        "focused": bool(row.is_focused),
        "sort_order": row.sort_order,
        "created_at": row.created_at,
        "template_uid": service.db.uid_for_id("goal_templates", row.template_id) if row.template_id else None,
        "milestones": [milestone(m, row.uid) for m in milestones],
        "progress": [done, total],
        "can_complete": service.can_complete_goal(row.id),
    }


def template(row) -> dict:
    try:
        milestones = json.loads(row.milestones_json or "[]")
    except ValueError:
        milestones = []
    specs = []
    for item in milestones if isinstance(milestones, list) else []:
        if isinstance(item, str):
            specs.append({"title": item, "note": ""})
        elif isinstance(item, dict):
            specs.append({"title": str(item.get("title", "")), "note": str(item.get("note", "") or "")})
    return {
        "uid": row.uid,
        "title": row.title,
        "notes": row.notes or "",
        "recurrence": row.recurrence,
        "recurrence_day": row.recurrence_day,
        "milestones": specs,
        "last_generated": row.last_generated,
        "active": bool(row.is_active),
        "sort_order": row.sort_order,
    }


def timer(service, *, now: Optional[datetime] = None, recovery_pending: bool = False) -> dict:
    """Engine snapshot of the timer; the client only animates elapsed text."""
    now = now or datetime.now()
    active = service.active_session
    subject_row = service.active_subject
    info = service.get_active_recovery_info(now=now) if active is not None else None
    foreign = [
        s for s in service.db.get_open_sessions()
        if s.device_id is not None and s.device_id != service.device_id
    ]
    return {
        "engine_now": now.isoformat(),
        "active": session(service.db, active, device_id=service.device_id) if active is not None else None,
        "subject": subject(service.db, subject_row) if subject_row is not None else None,
        "elapsed_seconds": service._elapsed_active_seconds(now) if active is not None else 0,
        "recovery": recovery(info) if info is not None and recovery_pending else None,
        "foreign_open": [session(service.db, s, device_id=service.device_id) for s in foreign],
    }


def recovery(info) -> dict:
    return {
        "subject_name": info.subject_name,
        "start": timeutils.to_iso(info.start),
        "last_active": timeutils.to_iso(info.last_active),
        "now": timeutils.to_iso(info.now),
        "duration_if_last_seconds": info.duration_if_last_seconds,
        "duration_if_now_seconds": info.duration_if_now_seconds,
        "gap_seconds": info.gap_seconds,
    }


def breakdown_bucket(codec, bucket: dict) -> dict:
    return {
        "date": str(bucket["date"]),
        "total_seconds": bucket["total_seconds"],
        "intensity_seconds": bucket["intensity_seconds"],
        "period_days": bucket.get("period_days"),
        "threshold_days": bucket.get("threshold_days"),
        "threshold_factor": bucket.get("threshold_factor"),
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


def agenda_block(codec, block: dict) -> dict:
    return {
        "uid": codec.uid_for_id("sessions", block["session_id"]) if block.get("session_id") is not None else None,
        "day": str(block["day"]),
        "subject_uid": codec.uid_for_id("tasks", block["subject_id"]),
        "subject_name": block["subject_name"],
        "color": block["color"],
        "start_h": block["start_h"],
        "end_h": block["end_h"],
        "duration_seconds": block["duration_seconds"],
    }


def day_session(codec, row: dict) -> dict:
    return {
        "uid": codec.uid_for_id("sessions", row["session_id"]) if row.get("session_id") is not None else None,
        "subject_uid": codec.uid_for_id("tasks", row["subject_id"]),
        "subject_name": row["subject_name"],
        "color": row["color"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_seconds": row["duration_seconds"],
        "open": row["end_time"] is None,
    }


def wire(db, table: str, row) -> dict:
    return sync_policy.to_wire(db, table, dict(row))
