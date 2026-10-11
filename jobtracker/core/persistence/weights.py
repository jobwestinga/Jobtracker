"""Weight rows and UID aliases on the Database connection."""

from __future__ import annotations

from .. import sync_policy
from .weight_codec import prepare_measurements


class WeightRepository:
    def __init__(self, database):
        self._database = database

    def list_rows(self):
        return self._database.connection.execute(
            "SELECT * FROM weight_measurements ORDER BY measured_at, id"
        ).fetchall()

    def resolve(self, uid):
        if not isinstance(uid, str) or not uid:
            raise ValueError("Invalid measurement identity")
        rows = self._database.connection.execute(
            "SELECT * FROM weight_measurements WHERE uid = ? OR uid = "
            "(SELECT weight_uid FROM weight_uid_aliases WHERE alias_uid = ?)", (uid, uid)
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("Measurement identity refers to conflicting entries")
        return rows[0] if rows else None

    def manual_on_day(self, day, *, excluding_id=None):
        # T..U is the complete normalized ISO date prefix, including date.max.
        query = (
            "SELECT * FROM weight_measurements WHERE source = 'manual' "
            "AND measured_at >= ? AND measured_at < ?"
        )
        params = (day + "T", day + "U")
        if excluding_id is not None:
            query += " AND id != ?"
            params += (excluding_id,)
        return self._database.connection.execute(
            query + " ORDER BY measured_at DESC, id DESC LIMIT 1", params
        ).fetchone()

    def add_alias(self, alias_uid, weight_uid):
        self._database.connection.execute(
            "INSERT INTO weight_uid_aliases (alias_uid, weight_uid) VALUES (?, ?) "
            "ON CONFLICT(alias_uid) DO NOTHING", (alias_uid, weight_uid)
        )

    def coalesce(self, source, destination):
        """Move identities before removing a daily row that shares its new day."""
        connection = self._database.connection
        connection.execute(
            "UPDATE weight_uid_aliases SET weight_uid = ? WHERE weight_uid = ?",
            (destination["uid"], source["uid"]),
        )
        connection.execute("DELETE FROM weight_measurements WHERE id = ?", (source["id"],))
        connection.execute(
            "INSERT INTO weight_uid_aliases (alias_uid, weight_uid) VALUES (?, ?) "
            "ON CONFLICT(alias_uid) DO UPDATE SET weight_uid = excluded.weight_uid",
            (source["uid"], destination["uid"]),
        )

    def update(self, row_id, timestamp, value):
        self._database.connection.execute(
            "UPDATE weight_measurements SET measured_at = ?, weight_kg = ? WHERE id = ?",
            (timestamp, value, row_id),
        )

    def insert_manual(self, uid, timestamp, value):
        cursor = self._database.connection.execute(
            "INSERT INTO weight_measurements (uid, measured_at, weight_kg, source) "
            "VALUES (?, ?, ?, 'manual')", (uid or sync_policy.new_uid(), timestamp, value)
        )
        return cursor.lastrowid

    def get(self, row_id):
        return self._database.connection.execute(
            "SELECT * FROM weight_measurements WHERE id = ?", (row_id,)
        ).fetchone()

    def delete(self, uid):
        cursor = self._database.connection.execute(
            "DELETE FROM weight_measurements WHERE uid = ?", (uid,)
        )
        return bool(cursor.rowcount)

    def import_rows(self, rows: list[dict]) -> dict:
        """Restore normalized identities without overwriting existing edits."""
        prepared = prepare_measurements(rows)
        added = 0
        with self._database.atomic():
            for item in prepared:
                # A UID alias is the same measurement, not a second import row.
                if self.resolve(item[0]) is not None:
                    continue
                existing = self._database.connection.execute(
                    "SELECT 1 FROM weight_measurements WHERE "
                    "measured_at = ? AND weight_kg = ? AND source = ?", item[1:4]
                ).fetchone()
                if existing:
                    continue
                self._database.connection.execute(
                    "INSERT INTO weight_measurements (uid, measured_at, weight_kg, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?)", item
                )
                added += 1
        return {"imported": added, "unchanged": len(prepared) - added}
