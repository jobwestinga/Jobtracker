"""Personal weight history, shared by imports and the authenticated API."""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from jobtracker.core import sync_policy, timeutils


def validate_measurement(measured_at, weight_kg, source="manual") -> tuple[str, float, str]:
    if not isinstance(measured_at, str):
        raise ValueError("Choose a valid measurement date")
    parsed = timeutils.parse_iso(measured_at)
    if parsed is None or parsed.tzinfo is not None:
        raise ValueError("Measurement date must be a local ISO date or datetime")
    if isinstance(weight_kg, bool):
        raise ValueError("Enter a weight in kilograms")
    try:
        value = float(str(weight_kg).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Enter a weight in kilograms") from None
    if not math.isfinite(value) or not 0 < value <= 1000:
        raise ValueError("Weight must be greater than 0 and at most 1000 kg")
    if source not in ("manual", "scale", "historic"):
        raise ValueError("Unknown weight source")
    return parsed.isoformat(timespec="seconds"), value, source


class WeightService:
    def __init__(self, database):
        self.db = database

    def list_measurements(self) -> list[dict]:
        rows = self.db.connection.execute(
            "SELECT * FROM weight_measurements ORDER BY measured_at, id"
        ).fetchall()
        return [sync_policy.to_wire(self.db, "weight_measurements", dict(row)) for row in rows]

    def _resolve(self, uid):
        if not isinstance(uid, str) or not uid:
            raise ValueError("Invalid measurement identity")
        rows = self.db.connection.execute(
            "SELECT * FROM weight_measurements WHERE uid = ? OR uid = "
            "(SELECT weight_uid FROM weight_uid_aliases WHERE alias_uid = ?)", (uid, uid)
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("Measurement identity refers to conflicting entries")
        return rows[0] if rows else None

    def upsert(self, measured_at, weight_kg, *, uid=None, weight_uid=None) -> dict:
        """Save a manual reading; another save that day edits the same reading.

        Imported scale readings keep their timestamps. Explicitly editing a row
        uses its stable uid, and repeated offline saves cannot create duplicates.
        A manual date correction onto an occupied day keeps that day's row and
        makes the moved row's identities aliases for it.
        """
        timestamp, value, _ = validate_measurement(measured_at, weight_kg)
        if uid is not None:
            try:
                uuid.UUID(uid)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Invalid measurement identity") from None
        with self.db.atomic():
            row = None
            if weight_uid is not None:
                row = self._resolve(weight_uid)
                if row is None:
                    return {"skipped": True, "reason": "measurement no longer exists"}
            elif uid:
                row = self._resolve(uid)
            if row is None:
                # Normalized ISO timestamps use T after the date. A prefix
                # range covers the whole day without overflowing on date.max.
                day = timestamp[:10]
                row = self.db.connection.execute(
                    "SELECT * FROM weight_measurements "
                    "WHERE source = 'manual' AND measured_at >= ? AND measured_at < ? "
                    "ORDER BY measured_at DESC, id DESC LIMIT 1",
                    (day + "T", day + "U"),
                ).fetchone()
            if row is not None:
                if uid and uid != row["uid"]:
                    existing_identity = self._resolve(uid)
                    if existing_identity is not None and existing_identity["id"] != row["id"]:
                        raise ValueError("Measurement identities refer to different entries")
                    self.db.connection.execute(
                        "INSERT INTO weight_uid_aliases (alias_uid, weight_uid) VALUES (?, ?) "
                        "ON CONFLICT(alias_uid) DO NOTHING", (uid, row["uid"])
                    )
                if row["source"] == "manual" and row["measured_at"][:10] != timestamp[:10]:
                    # An offline date correction can reach a destination day
                    # that another device has already filled. Keep its stable
                    # canonical row and move every identity for the old row
                    # with it, so queued edits/deletions still find the reading.
                    day = timestamp[:10]
                    destination = self.db.connection.execute(
                        "SELECT * FROM weight_measurements WHERE source = 'manual' "
                        "AND measured_at >= ? AND measured_at < ? AND id != ? "
                        "ORDER BY measured_at DESC, id DESC LIMIT 1",
                        (day + "T", day + "U", row["id"]),
                    ).fetchone()
                    if destination is not None:
                        self.db.connection.execute(
                            "UPDATE weight_uid_aliases SET weight_uid = ? WHERE weight_uid = ?",
                            (destination["uid"], row["uid"]),
                        )
                        self.db.connection.execute(
                            "DELETE FROM weight_measurements WHERE id = ?", (row["id"],)
                        )
                        self.db.connection.execute(
                            "INSERT INTO weight_uid_aliases (alias_uid, weight_uid) VALUES (?, ?) "
                            "ON CONFLICT(alias_uid) DO UPDATE SET weight_uid = excluded.weight_uid",
                            (row["uid"], destination["uid"]),
                        )
                        row = destination
                self.db.connection.execute(
                    "UPDATE weight_measurements SET measured_at = ?, weight_kg = ? WHERE id = ?",
                    (timestamp, value, row["id"]),
                )
                row_id = row["id"]
            else:
                cursor = self.db.connection.execute(
                    "INSERT INTO weight_measurements (uid, measured_at, weight_kg, source) "
                    "VALUES (?, ?, ?, 'manual')",
                    (uid or sync_policy.new_uid(), timestamp, value),
                )
                row_id = cursor.lastrowid
            result = self.db.connection.execute(
                "SELECT * FROM weight_measurements WHERE id = ?", (row_id,)
            ).fetchone()
            return {"measurement": sync_policy.to_wire(self.db, "weight_measurements", dict(result))}

    def delete(self, uid: str) -> bool:
        with self.db.atomic():
            row = self._resolve(uid)
            if row is None:
                return False
            cursor = self.db.connection.execute(
                "DELETE FROM weight_measurements WHERE uid = ?", (row["uid"],)
            )
            return bool(cursor.rowcount)

    def import_rows(self, rows: list[dict]) -> dict:
        """Import an already merged Weight_graph export without overwriting edits."""
        if not isinstance(rows, list):
            raise ValueError("Import must contain a list of measurements")
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each measurement must be an object")
            timestamp, value, source = validate_measurement(
                row.get("measured_at"), row.get("weight_kg"), row.get("source", "manual")
            )
            identity = row.get("uid")
            if identity is None:
                identity = str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"jobtracker:weight:{source}:{timestamp}"
                ))
            try:
                uuid.UUID(identity)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Invalid measurement identity") from None
            created_at = row.get("created_at")
            if created_at is None:
                created_at = datetime.now().isoformat()
            if not isinstance(created_at, str) or timeutils.parse_iso(created_at) is None:
                raise ValueError("Creation time must be a local ISO datetime")
            prepared.append((identity, timestamp, value, source, created_at))
        added = 0
        with self.db.atomic():
            for item in prepared:
                # Offline daily saves can give one measurement another client
                # uid. Restoring/importing that uid must recognize its alias,
                # rather than insert a second row competing with the original.
                if self._resolve(item[0]) is not None:
                    continue
                existing = self.db.connection.execute(
                    "SELECT 1 FROM weight_measurements WHERE "
                    "measured_at = ? AND weight_kg = ? AND source = ?", item[1:4]
                ).fetchone()
                if existing:
                    continue
                self.db.connection.execute(
                    "INSERT INTO weight_measurements (uid, measured_at, weight_kg, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?)", item
                )
                added += 1
        return {"imported": added, "unchanged": len(prepared) - added}
