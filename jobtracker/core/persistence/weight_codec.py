"""Validate and normalize weight rows for persistence and backup import."""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from .. import timeutils


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
    return parsed.isoformat(), value, source


def import_identity(timestamp: str, source: str) -> str:
    """Keep the original import seed while preserving timestamp precision.

    Existing exports used whole seconds to derive an implicit UUID. Changing
    that seed when retaining fractions would turn a reimport into a new row.
    Explicit measurement UUIDs still distinguish readings within one second.
    """
    legacy_timestamp = timestamp.split(".", 1)[0]
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"jobtracker:weight:{source}:{legacy_timestamp}"
    ))


def prepare_measurements(rows: list[dict]) -> list[tuple]:
    """Validate the complete import before the first write."""
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
            identity = import_identity(timestamp, source)
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
    return prepared
