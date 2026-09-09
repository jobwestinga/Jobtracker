"""The server's change feed: what changed, in what order.

Clients pull ``/sync/pull?since=<seq>`` and apply what comes back to their local
mirror. Two properties make that safe:

* **The feed is produced by SQLite triggers**, not by application code, so no
  write path can forget to record a change — including code added later.
* **``seq`` is monotonic**, so a client can tell the difference between "nothing
  changed" and "I missed something". A gap means re-download everything rather
  than patch around the hole.

The feed records only *that* a row changed (table + uid). The pull endpoint joins
the current row in when it answers, so a client can never receive a stale payload
that was serialised at trigger time.

Deletes are the exception: there is no row left to join, so the trigger archives
the whole row as JSON into ``deleted_rows``. Nothing is ever actually destroyed,
which is what makes a bad delete diagnosable and reversible after the fact.
"""

from __future__ import annotations

import logging
import sqlite3

from jobtracker.core import sync_policy

logger = logging.getLogger("jobtracker.server")

FEED_SCHEMA = """
-- row_uid is nullable ON PURPOSE. A row's uid is filled by an AFTER INSERT
-- trigger, so at the moment an INSERT trigger fires, NEW.uid is still NULL.
-- Recording the rowid too (never reused: every synced table is INTEGER PRIMARY
-- KEY AUTOINCREMENT) means the feed is correct regardless of trigger order, and
-- the uid is resolved when the feed is read.
CREATE TABLE IF NOT EXISTS change_feed (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_id     INTEGER NOT NULL,
    row_uid    TEXT,
    op         TEXT NOT NULL CHECK (op IN ('upsert', 'delete')),
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- No index on `seq`: it is INTEGER PRIMARY KEY, so it already IS the rowid,
-- and a second index on it only made every insert slower. The pull query groups
-- and correlates on (table_name, row_id), which is what this covers.
CREATE INDEX IF NOT EXISTS idx_change_feed_row ON change_feed(table_name, row_id);

CREATE TABLE IF NOT EXISTS deleted_rows (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_uid    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    deleted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applied_ops (
    op_id      TEXT PRIMARY KEY,
    op_name    TEXT NOT NULL,
    device_id  TEXT,
    response   TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    cur = connection.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [row["name"] for row in cur.fetchall()]


def install(connection: sqlite3.Connection) -> None:
    """Create the feed tables and (re)create the capture triggers.

    Triggers embed the table's column list, so they are dropped and rebuilt on
    every start: a schema migration that adds a column is picked up automatically
    instead of silently archiving an out-of-date shape on delete.
    """
    connection.executescript(FEED_SCHEMA)
    # Databases created before this was noticed carry an index on `seq`, which
    # is the rowid; it costs a write on every change and buys nothing.
    connection.execute("DROP INDEX IF EXISTS idx_change_feed_seq")

    for table in sync_policy.SYNCED_TABLES:
        cols = _columns(connection, table)
        if "uid" not in cols:
            raise RuntimeError(
                f"{table} has no uid column; run the desktop migration first"
            )
        # json_object('col', OLD.col, ...) — built here rather than hand-written
        # so it cannot drift from the real schema.
        old_json = ", ".join(f"'{c}', OLD.{c}" for c in cols)

        for suffix in ("ins", "upd", "del"):
            connection.execute(f"DROP TRIGGER IF EXISTS trg_feed_{table}_{suffix}")

        connection.execute(
            f"""
            CREATE TRIGGER trg_feed_{table}_ins AFTER INSERT ON {table}
            BEGIN
                INSERT INTO change_feed (table_name, row_id, row_uid, op)
                VALUES ('{table}', NEW.id, NEW.uid, 'upsert');
            END
            """
        )
        # WHEN excludes exactly one thing: the UPDATE that the uid trigger fires
        # immediately after an INSERT to fill in a missing uid. The insert's own
        # event already covers that row, so logging it again doubled the size of
        # the feed for no information. Any other update still lands here.
        connection.execute(
            f"""
            CREATE TRIGGER trg_feed_{table}_upd AFTER UPDATE ON {table}
            WHEN NOT (OLD.uid IS NULL AND NEW.uid IS NOT NULL)
            BEGIN
                INSERT INTO change_feed (table_name, row_id, row_uid, op)
                VALUES ('{table}', NEW.id, NEW.uid, 'upsert');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER trg_feed_{table}_del AFTER DELETE ON {table}
            BEGIN
                INSERT INTO deleted_rows (table_name, row_uid, payload)
                VALUES ('{table}', OLD.uid, json_object({old_json}));
                INSERT INTO change_feed (table_name, row_id, row_uid, op)
                VALUES ('{table}', OLD.id, OLD.uid, 'delete');
            END
            """
        )

    connection.commit()
    logger.info("Change feed installed for %d tables", len(sync_policy.SYNCED_TABLES))


def current_seq(connection: sqlite3.Connection) -> int:
    cur = connection.cursor()
    cur.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM change_feed")
    return int(cur.fetchone()["seq"])


def changes_since(
    database, since: int, limit: int = 5000
) -> tuple[list[dict], int]:
    """Rows changed after ``since``, newest state included, plus the new cursor.

    Collapses repeated changes to the same row: a client only needs the current
    state, not the history of how it got there.

    Rows go out through :func:`sync_policy.to_wire`, so local integer ids and
    foreign keys never leave this machine.
    """
    connection = database.connection
    cur = connection.cursor()
    # Group by rowid, not uid: an insert's feed entry has no uid yet. Rowids are
    # never recycled here (all synced tables are AUTOINCREMENT), so one rowid is
    # one row forever. The last entry per row decides upsert vs delete.
    cur.execute(
        """
        SELECT table_name,
               row_id,
               MAX(seq) AS seq,
               (SELECT f2.op FROM change_feed f2
                 WHERE f2.table_name = f1.table_name AND f2.row_id = f1.row_id
              ORDER BY f2.seq DESC LIMIT 1) AS op,
               (SELECT f3.row_uid FROM change_feed f3
                 WHERE f3.table_name = f1.table_name AND f3.row_id = f1.row_id
                   AND f3.row_uid IS NOT NULL
              ORDER BY f3.seq DESC LIMIT 1) AS row_uid
          FROM change_feed f1
         WHERE seq > ?
      GROUP BY table_name, row_id
      ORDER BY seq ASC
         LIMIT ?
        """,
        (since, limit),
    )
    entries = [dict(row) for row in cur.fetchall()]

    # Fetch the changed rows in bulk, one query per table, instead of one query
    # per row. A first sync moves ~1800 rows; that was ~1800 round trips.
    wanted: dict[str, list[int]] = {}
    for entry in entries:
        if entry["op"] == "upsert":
            wanted.setdefault(entry["table_name"], []).append(entry["row_id"])
    fetched: dict[tuple[str, int], dict] = {}
    for table, ids in wanted.items():
        for chunk_start in range(0, len(ids), 400):   # stay under SQLite's limit
            chunk = ids[chunk_start:chunk_start + 400]
            placeholders = ",".join("?" for _ in chunk)
            for row in cur.execute(
                f"SELECT * FROM {table} WHERE id IN ({placeholders})", chunk
            ).fetchall():
                fetched[(table, row["id"])] = dict(row)

    codec = sync_policy.WireCodec(database)
    changes: list[dict] = []
    highest = since
    for entry in entries:
        highest = max(highest, int(entry["seq"]))
        table = entry["table_name"]
        row = fetched.get((table, entry["row_id"])) if entry["op"] == "upsert" else None
        # Prefer the row's live uid; fall back to the one recorded in the feed,
        # which is the only source once the row is gone.
        uid = (row["uid"] if row is not None else None) or entry["row_uid"]
        if uid is None:
            continue
        item = {
            "seq": int(entry["seq"]),
            "table": sync_policy.API_NAMES[table],
            "uid": uid,
            "op": entry["op"],
        }
        if entry["op"] == "upsert":
            if row is None:
                # Created and deleted between two pulls; the delete entry that
                # follows carries the truth, so skip the phantom upsert.
                continue
            item["row"] = codec.to_wire(table, row)
        changes.append(item)

    return changes, highest
