"""Templates operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError, _target_uid, _adopt_uid, _row,
)
# Generation is authoritative and additive: every client pulls the same period
# instances rather than generating a competing local copy.


def add_template(svc, p):
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

def update_template(svc, p):
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

def set_template_active(svc, p):
    tid = _target_uid(svc.db, "goal_templates", p["template_uid"], "template")
    svc.set_goal_template_active(tid, bool(p["active"]))
    return {"template": _row(svc.db, "goal_templates", tid)}

def delete_template(svc, p):
    svc.delete_goal_template(
        _target_uid(svc.db, "goal_templates", p["template_uid"], "template")
    )
    return {"ok": True}

def generate_due_goals(svc, p):
    """Create this period's goal instances. Idempotent within a period, so a
    client may call it on every launch without producing duplicates."""
    created = svc.generate_due_goal_instances()
    return {
        "created": [svc.db.uid_for_id("todo_tasks", gid) for gid in created],
    }
