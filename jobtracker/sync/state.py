"""Local sync bookkeeping: the outbox and the mirror cursor.

Both tables live in the desktop's own database but are **not** synced — they
describe this machine's relationship with the server, not the user's data.

The outbox is the heart of offline tolerance. A write made while the server is
unreachable is recorded here, in order, and replayed later. Two rules keep it
from corrupting anything:

* **Strict order.** Operations replay in the order they were created. A failing
  operation blocks the ones behind it rather than being skipped, because a queue
  that reorders itself is how "delete then recreate" turns into "recreate then
  delete".
* **A stable op_id per entry**, generated once when queued. Retrying sends the
  same id, and the server replays its recorded result instead of applying the
  operation twice.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

from ..core import sync_policy

logger = logging.getLogger("jobtracker")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id      TEXT NOT NULL UNIQUE,
    op_name    TEXT NOT NULL,
    params     TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Keys used in sync_state.
LAST_SEQ = "last_seq"
LAST_SYNC_AT = "last_sync_at"
LAST_ERROR = "last_error"


def install(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def get(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    cur = connection.cursor()
    cur.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def set_value(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    connection.commit()


def last_seq(connection: sqlite3.Connection) -> int:
    try:
        return int(get(connection, LAST_SEQ, "0") or 0)
    except ValueError:
        return 0


def enqueue(
    connection: sqlite3.Connection,
    op_name: str,
    params: dict[str, Any],
    op_id: Optional[str] = None,
) -> str:
    """Queue one operation. Returns the op_id it will always be sent under."""
    op_id = op_id or sync_policy.new_uid()
    connection.execute(
        "INSERT INTO sync_outbox (op_id, op_name, params) VALUES (?, ?, ?)",
        (op_id, op_name, json.dumps(params, default=str)),
    )
    connection.commit()
    return op_id


def pending(connection: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Queued operations, oldest first — the order they must be replayed in."""
    cur = connection.cursor()
    cur.execute(
        "SELECT id, op_id, op_name, params, attempts, last_error "
        "FROM sync_outbox ORDER BY id ASC LIMIT ?",
        (limit,),
    )
    return [
        {
            "id": int(row["id"]),
            "op_id": row["op_id"],
            "op": row["op_name"],
            "params": json.loads(row["params"]),
            "attempts": int(row["attempts"]),
            "last_error": row["last_error"],
        }
        for row in cur.fetchall()
    ]


def pending_count(connection: sqlite3.Connection) -> int:
    cur = connection.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM sync_outbox")
    return int(cur.fetchone()["n"])


def drop(connection: sqlite3.Connection, entry_id: int) -> None:
    connection.execute("DELETE FROM sync_outbox WHERE id = ?", (entry_id,))
    connection.commit()


def record_failure(connection: sqlite3.Connection, entry_id: int, error: str) -> None:
    connection.execute(
        "UPDATE sync_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
        (error[:500], entry_id),
    )
    connection.commit()
