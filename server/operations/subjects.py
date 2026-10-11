"""Subjects operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError, _target_uid, _existing_uids, _adopt_uid, _row, _model_row,
)

def add_subject(svc, p):
    subject = svc.add_subject(p["name"], p.get("color", "#3B82F6"), p.get("notes", ""))
    if subject is None:
        raise OpError("subject could not be created (duplicate or empty name)")
    _adopt_uid(svc.db, "tasks", subject.id, p.get("uid"))
    return {"subject": _row(svc.db, "tasks", subject.id)}

def update_subject(svc, p):
    sid = _target_uid(svc.db, "tasks", p["subject_uid"], "subject")
    subject = svc.update_subject(sid, p["name"], p["color"], p.get("notes", ""))
    if subject is None:
        raise OpError("subject could not be updated (duplicate or empty name)")
    return {"subject": _model_row(svc.db, "tasks", subject)}

def archive_subject(svc, p):
    svc.archive_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}

def unarchive_subject(svc, p):
    svc.unarchive_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}

def delete_subject(svc, p):
    svc.delete_subject(_target_uid(svc.db, "tasks", p["subject_uid"], "subject"))
    return {"ok": True}

def set_subject_order(svc, p):
    ids = _existing_uids(svc.db, "tasks", p["subject_uids"], "subject")
    svc.set_subject_order(ids, archived=bool(p.get("archived", False)))
    return {"ok": True}
