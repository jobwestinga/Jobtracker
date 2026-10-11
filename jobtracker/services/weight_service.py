"""Personal weight history, shared by imports and the authenticated API."""

from __future__ import annotations

import uuid

from jobtracker.core import sync_policy
from jobtracker.core.persistence.weight_codec import validate_measurement


class WeightService:
    def __init__(self, database):
        self.db = database
        self._repository = database._weights

    def list_measurements(self) -> list[dict]:
        rows = self._repository.list_rows()
        return [sync_policy.to_wire(self.db, "weight_measurements", dict(row)) for row in rows]

    def _resolve(self, uid):
        return self._repository.resolve(uid)

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
                row = self._repository.manual_on_day(day)
            if row is not None:
                if uid and uid != row["uid"]:
                    existing_identity = self._resolve(uid)
                    if existing_identity is not None and existing_identity["id"] != row["id"]:
                        raise ValueError("Measurement identities refer to different entries")
                    self._repository.add_alias(uid, row["uid"])
                if row["source"] == "manual" and row["measured_at"][:10] != timestamp[:10]:
                    # An offline date correction can reach a destination day
                    # that another device has already filled. Keep its stable
                    # canonical row and move every identity for the old row
                    # with it, so queued edits/deletions still find the reading.
                    day = timestamp[:10]
                    destination = self._repository.manual_on_day(day, excluding_id=row["id"])
                    if destination is not None:
                        self._repository.coalesce(row, destination)
                        row = destination
                self._repository.update(row["id"], timestamp, value)
                row_id = row["id"]
            else:
                row_id = self._repository.insert_manual(uid, timestamp, value)
            result = self._repository.get(row_id)
            return {"measurement": sync_policy.to_wire(self.db, "weight_measurements", dict(result))}

    def delete(self, uid: str) -> bool:
        with self.db.atomic():
            row = self._resolve(uid)
            if row is None:
                return False
            return self._repository.delete(row["uid"])

    def import_rows(self, rows: list[dict]) -> dict:
        """Import an already merged Weight_graph export without overwriting edits."""
        return self._repository.import_rows(rows)
