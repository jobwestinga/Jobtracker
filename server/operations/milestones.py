"""Milestones operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError, _require_uid, _target_uid, _existing_uids, _adopt_uid, _row, _model_row,
)

def add_milestone(svc, p):
    gid = _require_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    milestone = svc.add_milestone(gid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be created (empty title?)")
    _adopt_uid(svc.db, "milestones", milestone.id, p.get("uid"))
    return {"milestone": _row(svc.db, "milestones", milestone.id)}

def update_milestone(svc, p):
    mid = _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    milestone = svc.update_milestone(mid, p["title"], p.get("note", ""))
    if milestone is None:
        raise OpError("milestone could not be updated")
    return {"milestone": _model_row(svc.db, "milestones", milestone)}

def set_milestone_done(svc, p):
    mid = _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    svc.set_milestone_done(mid, bool(p["done"]))
    return {"milestone": _row(svc.db, "milestones", mid)}

def delete_milestone(svc, p):
    svc.delete_milestone(
        _target_uid(svc.db, "milestones", p["milestone_uid"], "milestone")
    )
    return {"ok": True}

def set_milestone_order(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    ids = _existing_uids(svc.db, "milestones", p["milestone_uids"], "milestone")
    svc.set_milestone_order(gid, ids)
    return {"ok": True}
