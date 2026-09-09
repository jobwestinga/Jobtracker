"""What syncs, and how rows are identified across machines.

This is the single place that answers "is this table/setting shared?" and
"what identifies this row to another machine?". Both the desktop app and the
server import it, so the two can never disagree.

Identity rule (load-bearing — see CLAUDE.md):

    Integer row ids are PRIVATE to one database and never cross the network.
    ``uid`` is the only identity that travels.

Each database assigns its own integer primary keys and keeps using them for
foreign keys, Qt item data, and every existing query — so nothing in the app
had to change. A row's ``uid`` is minted once (by whoever creates the row) and
is then the same string on every machine, which is what makes transferring a
row an exact match instead of a guess.
"""

from __future__ import annotations

import uuid

# Tables whose rows are shared between machines. Order matters: parents before
# children, so applying rows in this order never violates a foreign key.
SYNCED_TABLES: tuple[str, ...] = (
    "tasks",          # subjects
    "todo_tasks",     # goals
    "goal_templates",
    "sessions",       # FK -> tasks
    "milestones",     # FK -> todo_tasks
)

# Settings are mostly per-machine UI state: the theme, which graph range was
# last open, the agenda hour window, the goal ordering mode. Syncing those
# would mean the phone dictating the Mac's window layout. Only genuine
# preferences that describe *the data* belong here.
SYNCED_SETTING_KEYS: frozenset[str] = frozenset({"day_start_time"})

# Settings that must never leave the machine that wrote them, even if a future
# change makes the allowlist above more permissive. ``device_id`` identifies
# THIS install; copying it to another machine would make two devices claim the
# same running timer.
DEVICE_LOCAL_SETTING_KEYS: frozenset[str] = frozenset({"device_id"})

# Setting key holding this install's device id.
DEVICE_ID_SETTING = "device_id"


# The API speaks clean names; SQL keeps the legacy ones. This is how the good
# nomenclature is reached without the risky `tasks` -> `subjects` table rename.
API_NAMES: dict[str, str] = {
    "tasks": "subjects",
    "todo_tasks": "goals",
    "goal_templates": "templates",
    "sessions": "sessions",
    "milestones": "milestones",
}
TABLE_FOR_API_NAME: dict[str, str] = {v: k for k, v in API_NAMES.items()}

# Foreign keys hold LOCAL integer ids, which are meaningless on another machine.
# Each one is translated to the referenced row's uid on the way out and back to a
# local id on the way in: column -> (referenced table, name used on the wire).
FOREIGN_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    "sessions": {"task_id": ("tasks", "subject_uid")},
    "milestones": {"goal_id": ("todo_tasks", "goal_uid")},
    "todo_tasks": {"template_id": ("goal_templates", "template_uid")},
}

# Columns that are private to one database and must never be sent.
PRIVATE_COLUMNS: frozenset[str] = frozenset({"id"})


class WireCodec:
    """Translates rows to and from the wire, resolving ids in bulk.

    ``to_wire`` on its own does one ``uid_for_id`` query per foreign key per row.
    That is a query per row: building a snapshot of ~1800 rows cost ~1800
    queries. This loads each referenced table's id↔uid mapping once and answers
    from memory, turning that into one query per referenced table.

    Short-lived by design — build one per request or per sync batch. It caches,
    so anything created after it was built is looked up individually rather than
    being wrongly reported as missing.
    """

    def __init__(self, database):
        self.db = database
        self._id_to_uid: dict[str, dict] = {}
        self._uid_to_id: dict[str, dict] = {}

    def _load(self, table: str) -> None:
        if table in self._id_to_uid:
            return
        cur = self.db.connection.cursor()
        cur.execute(f"SELECT id, uid FROM {table}")
        rows = cur.fetchall()
        self._id_to_uid[table] = {r["id"]: r["uid"] for r in rows}
        self._uid_to_id[table] = {r["uid"]: r["id"] for r in rows if r["uid"]}

    def uid_for_id(self, table: str, row_id):
        if row_id is None:
            return None
        self._load(table)
        cached = self._id_to_uid[table].get(row_id)
        if cached is None:
            # Created since this codec was built; ask directly and remember.
            cached = self.db.uid_for_id(table, row_id)
            if cached is not None:
                self._id_to_uid[table][row_id] = cached
                self._uid_to_id[table][cached] = row_id
        return cached

    def id_for_uid(self, table: str, uid):
        if not uid:
            return None
        self._load(table)
        cached = self._uid_to_id[table].get(uid)
        if cached is None:
            cached = self.db.id_for_uid(table, uid)
            if cached is not None:
                self._uid_to_id[table][uid] = cached
                self._id_to_uid[table][cached] = uid
        return cached

    def to_wire(self, table: str, row: dict) -> dict:
        return to_wire(self, table, row)

    def from_wire(self, table: str, payload: dict) -> dict:
        return from_wire(self, table, payload)


def to_wire(database, table: str, row: dict) -> dict:
    """Convert a raw DB row into the shape other machines understand.

    Strips local integer ids and rewrites foreign keys as uids. If this function
    ever lets an integer id through, two machines will eventually disagree about
    which row is which — hence the test that asserts no payload contains one.
    """
    fks = FOREIGN_KEYS.get(table, {})
    wire: dict = {}
    for column, value in row.items():
        if column in PRIVATE_COLUMNS:
            continue
        if column in fks:
            ref_table, wire_name = fks[column]
            wire[wire_name] = (
                database.uid_for_id(ref_table, value) if value is not None else None
            )
            continue
        wire[column] = value
    return wire


def from_wire(database, table: str, payload: dict) -> dict:
    """Inverse of :func:`to_wire`, for applying a row into a local database.

    A foreign key naming a uid this database has never seen resolves to None
    rather than raising: the referenced row may simply arrive later in the same
    batch, and the caller re-checks once the batch is applied.
    """
    fks = FOREIGN_KEYS.get(table, {})
    by_wire_name = {wire_name: (col, ref) for col, (ref, wire_name) in fks.items()}
    row: dict = {}
    for key, value in payload.items():
        if key in PRIVATE_COLUMNS:
            continue
        if key in by_wire_name:
            column, ref_table = by_wire_name[key]
            row[column] = (
                database.id_for_uid(ref_table, value) if value is not None else None
            )
            continue
        row[key] = value
    return row


def new_uid() -> str:
    """A fresh row identity. UUID4 — collision-free without coordination, which
    is what lets an offline client create a row that the server accepts as-is."""
    return str(uuid.uuid4())


# UUID4 built in SQL, used by the AFTER INSERT triggers so that a row created by
# ANY code path — including future code, and including `import_data()` — gets a
# uid without that code having to remember. Shape: 8-4-4-4-12 with the version
# nibble pinned to 4 and the variant nibble to one of 8/9/a/b.
UUID4_SQL = (
    "lower("
    "hex(randomblob(4)) || '-' || "
    "hex(randomblob(2)) || '-4' || "
    "substr(hex(randomblob(2)), 2) || '-' || "
    "substr('89ab', abs(random()) % 4 + 1, 1) || "
    "substr(hex(randomblob(2)), 2) || '-' || "
    "hex(randomblob(6))"
    ")"
)


def uid_trigger_name(table: str) -> str:
    return f"trg_{table}_uid"


def uid_trigger_sql(table: str) -> str:
    """Backfill a missing uid immediately after insert.

    ``WHEN NEW.uid IS NULL`` is what lets a restored backup keep the uids it
    already carries: an explicit uid is preserved, only a missing one is minted.
    """
    return (
        f"CREATE TRIGGER IF NOT EXISTS {uid_trigger_name(table)} "
        f"AFTER INSERT ON {table} "
        "WHEN NEW.uid IS NULL "
        "BEGIN "
        f"UPDATE {table} SET uid = {UUID4_SQL} WHERE id = NEW.id; "
        "END"
    )
