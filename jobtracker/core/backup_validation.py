"""Validate the shape of a backup before its additive restore starts."""

from __future__ import annotations

BACKUP_TABLES = (
    "subjects", "tasks", "sessions", "todo_tasks", "goal_templates",
    "milestones", "settings", "weights", "weight_aliases",
)
MAX_BACKUP_RECORDS = 100_000


def validate_backup(data: dict) -> None:
    """Accept legacy row fields, while rejecting malformed or oversized tables."""
    if not isinstance(data, dict):
        raise ValueError("backup data must be an object")
    count = 0
    for table in BACKUP_TABLES:
        if table not in data:
            continue
        rows = data[table]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"backup {table} must be a list of objects")
        count += len(rows)
        if count > MAX_BACKUP_RECORDS:
            raise ValueError(f"backup must contain at most {MAX_BACKUP_RECORDS} records")
