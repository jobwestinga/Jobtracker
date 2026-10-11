"""Backup operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from jobtracker.core import sync_policy
from jobtracker.core.backup_validation import validate_backup

def import_backup(svc, p):
    data = p["data"]
    validate_backup(data)
    # The existing importer maps legacy parent IDs and merges by contents.
    # A queued mirror export may have stale contents for a known UID: replace
    # those parents with their authoritative rows while retaining the export's
    # IDs for mapping its new children. Known children need no second import.
    merged = dict(data)
    for key, table in (
        ("subjects", "tasks"), ("tasks", "tasks"),
        ("goal_templates", "goal_templates"), ("todo_tasks", "todo_tasks"),
    ):
        if key not in data:
            continue
        canonical = {
            row["uid"]: dict(row)
            for row in svc.db.connection.execute(f"SELECT * FROM {table}")
        }
        rows = []
        for incoming in data[key]:
            existing = canonical.get(incoming.get("uid"))
            rows.append({**existing, "id": incoming.get("id")} if existing else incoming)
        merged[key] = rows
    for table in ("sessions", "milestones"):
        existing = {row["uid"] for row in svc.db.connection.execute(f"SELECT uid FROM {table}")}
        merged[table] = [row for row in data.get(table, []) if row.get("uid") not in existing]
    merged["settings"] = [
        row for row in data.get("settings", [])
        if row.get("key") in sync_policy.SYNCED_SETTING_KEYS
    ]
    svc.import_data(merged)
    return {"imported": True}
