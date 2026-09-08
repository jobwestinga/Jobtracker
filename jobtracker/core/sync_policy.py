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
