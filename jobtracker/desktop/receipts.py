"""Durable local request receipts for native-client mutations.

A receipt binds a native ``request_id`` to its canonical command, parameters
and store identity, and records the committed outcome. The receipt, the domain
mutation and any existing sync-outbox entries commit in one ``Database.atomic``
scope, so a reply lost after commit is answered from the receipt instead of
running the command again.

The table is local bookkeeping, like ``sync_outbox``: it is not a synced table,
not part of backups and not touched by mirror replacement. It is not another
pending-write queue either; nothing is ever replayed from it. Fingerprints and
safe result DTOs are stored, never credentials (configuration commands are not
receipt-backed).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jobtracker")

TABLE = "desktop_request_receipts"

SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_request_receipts (
    request_id  TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""


class ReceiptConflict(ValueError):
    """The request ID already belongs to a different command or parameters."""


def fingerprint(command: str, params: dict, store_id: str) -> str:
    canonical = json.dumps(
        {"command": command, "params": params, "store": store_id},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)
    ).fetchone()
    return row is not None


def install(connection: sqlite3.Connection, *, backup_dir: Optional[Path] = None) -> Optional[Path]:
    """Create the receipts table additively; back up an existing store first.

    Returns the online backup path when one was taken. A store that already has
    the table, or a brand-new empty store, needs no backup.
    """
    if table_exists(connection):
        return None
    backup_path = None
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    # Weight, Goals and local setup are independent of Subjects. An existing
    # store with no subject rows still needs a pre-migration safety copy.
    data_tables = ("tasks", "sessions", "todo_tasks", "milestones", "goal_templates",
                   "weight_measurements", "weight_uid_aliases", "settings", "sync_outbox")
    has_data = any(table in tables and connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   for table in data_tables)
    if backup_dir is not None and has_data:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"pre-native-receipts_{stamp}.db"
        destination = sqlite3.connect(str(backup_path))
        try:
            connection.backup(destination)
        finally:
            destination.close()
        logger.info("Backed up store before adding native receipts: %s", backup_path.name)
    connection.executescript(SCHEMA)
    connection.commit()
    return backup_path


def lookup(connection: sqlite3.Connection, request_id: str, digest: str) -> Optional[tuple[str, dict]]:
    """Saved ``(outcome, result)`` for this request, or None if never committed."""
    row = connection.execute(
        f"SELECT fingerprint, outcome, result FROM {TABLE} WHERE request_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        return None
    if row["fingerprint"] != digest:
        raise ReceiptConflict("request_id already belongs to a different request")
    return row["outcome"], json.loads(row["result"])


def store(connection: sqlite3.Connection, request_id: str, command: str, digest: str,
          outcome: str, result: dict) -> None:
    connection.execute(
        f"INSERT INTO {TABLE} (request_id, command, fingerprint, outcome, result, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (request_id, command, digest, outcome,
         json.dumps(result, separators=(",", ":"), default=str),
         datetime.now().isoformat()),
    )
