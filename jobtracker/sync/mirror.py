"""Apply server wire rows to one local mirror connection.

Ordering, foreign-key resolution, local heartbeat preservation and column lookup
belong here. The engine owns page transactions, cursors and unsent-edit guards;
these helpers do not commit or create another database/cache.
"""

from __future__ import annotations

import logging
import sqlite3

from ..core import sync_policy, timeutils

logger = logging.getLogger("jobtracker")


def upsert(
    database, api_table: str, uid: str, wire_row: dict, codec=None, heartbeat=None,
    *, device_id: str, column_names,
) -> None:
    """Write one server row into the mirror, keyed by uid.

    Foreign keys arrive as uids and are translated back to this database's
    local ids. Missing parents roll the page back and require a complete
    snapshot, so even nullable relationships cannot silently lose their link.
    """
    table = sync_policy.TABLE_FOR_API_NAME.get(api_table)
    if table is None:
        logger.warning("Ignoring unknown table from server: %s", api_table)
        return

    resolver = codec if codec is not None else database
    row = sync_policy.from_wire(resolver, table, dict(wire_row))
    # Nullable foreign keys (notably a goal's template) must not silently
    # become NULL just because the parent is on a later feed page. Counts
    # and uid hashes would consider that damaged relationship healthy.
    for column, (_parent, wire_name) in sync_policy.FOREIGN_KEYS.get(table, {}).items():
        if wire_row.get(wire_name) is not None and row.get(column) is None:
            raise sqlite3.IntegrityError(f"missing parent for {api_table}.{wire_name}")
    row["uid"] = uid

    columns = [c for c in column_names(table) if c in row]
    local_id = (
        codec.id_for_uid(table, uid) if codec is not None
        else database.id_for_uid(table, uid)
    )
    if table == "sessions" and row.get("end_time") is None and row.get("device_id") in (None, device_id):
        # Desktop heartbeats deliberately stay local. Pulling the server's
        # older start-time heartbeat must not turn an hour of work into a
        # false recovery gap the next time the app opens.
        if heartbeat is None and local_id is not None:
            local = database.connection.execute(
                "SELECT last_active_at FROM sessions WHERE id = ?", (local_id,)
            ).fetchone()
            heartbeat = local["last_active_at"] if local else None
        local_stamp = timeutils.parse_iso(heartbeat)
        remote_stamp = timeutils.parse_iso(row.get("last_active_at"))
        started = timeutils.parse_iso(row.get("start_time"))
        if local_stamp is not None and started is not None and local_stamp >= started and (
            remote_stamp is None or local_stamp > remote_stamp
        ):
            row["last_active_at"] = heartbeat
    values = [row[c] for c in columns]
    cur = database.connection.cursor()
    if local_id is None:
        placeholders = ", ".join("?" for _ in columns)
        cur.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    else:
        assignments = ", ".join(f"{c} = ?" for c in columns)
        cur.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?", [*values, local_id]
        )


def delete(database, api_table: str, uid: str, codec=None) -> bool:
    table = sync_policy.TABLE_FOR_API_NAME.get(api_table)
    if table is None:
        return False
    local_id = (
        codec.id_for_uid(table, uid) if codec is not None
        else database.id_for_uid(table, uid)
    )
    if local_id is None:
        return False
    database.connection.execute(f"DELETE FROM {table} WHERE id = ?", (local_id,))
    return True


def apply_order(change: dict) -> tuple:
    """Sort key: upserts parent-first, deletes child-first, then by seq.

    ``SYNCED_TABLES`` is already listed parents-first, which is what makes
    this a lookup rather than a graph walk.
    """
    table = sync_policy.TABLE_FOR_API_NAME.get(change.get("table"))
    try:
        rank = sync_policy.SYNCED_TABLES.index(table)
    except ValueError:
        rank = len(sync_policy.SYNCED_TABLES)
    if change.get("op") == "delete":
        # Deletes run after upserts and from the leaves inwards.
        return (1, -rank, int(change.get("seq", 0)))
    return (0, rank, int(change.get("seq", 0)))


def columns(database, table: str, column_cache: dict[str, list[str]]) -> list[str]:
    cached = column_cache.get(table)
    if cached is None:
        cur = database.connection.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cached = [r["name"] for r in cur.fetchall() if r["name"] != "id"]
        column_cache[table] = cached
    return cached
