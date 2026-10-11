"""Goals operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError, _target_uid, _existing_uids, _adopt_uid, _row, _model_row,
)

def add_goal(svc, p):
    goal = svc.add_todo_task(p["name"], p.get("notes", ""), p.get("deadline"))
    if goal is None:
        raise OpError("goal could not be created (empty name?)")
    _adopt_uid(svc.db, "todo_tasks", goal.id, p.get("uid"))
    return {"goal": _row(svc.db, "todo_tasks", goal.id)}

def update_goal(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    goal = svc.update_todo_task(gid, p["name"], p.get("notes", ""), p.get("deadline"))
    if goal is None:
        raise OpError("goal could not be updated")
    return {"goal": _model_row(svc.db, "todo_tasks", goal)}

def delete_goal(svc, p):
    svc.delete_todo_task(_target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal"))
    return {"ok": True}

def complete_goal(svc, p):
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

def uncomplete_goal(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    svc.uncomplete_goal(gid)
    return {"goal": _row(svc.db, "todo_tasks", gid)}

def toggle_goal_focused(svc, p):
    gid = _target_uid(svc.db, "todo_tasks", p["goal_uid"], "goal")
    focused = svc.toggle_goal_focused(gid)
    return {"focused": focused, "goal": _row(svc.db, "todo_tasks", gid)}

def set_goal_order(svc, p):
    ids = _existing_uids(svc.db, "todo_tasks", p["goal_uids"], "goal")
    svc.set_todo_task_order(ids, completed=bool(p.get("completed", False)))
    return {"ok": True}
