"""Weight operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from .common import (
    OpError,
)

def upsert_weight(svc, p):
    from jobtracker.services.weight_service import WeightService
    return WeightService(svc.db).upsert(
        p["measured_at"], p["weight_kg"], uid=p.get("uid"), weight_uid=p.get("weight_uid")
    )

def delete_weight(svc, p):
    from jobtracker.services.weight_service import WeightService
    return {"deleted": WeightService(svc.db).delete(p["weight_uid"])}

def import_weights(svc, p):
    from jobtracker.services.weight_service import WeightService
    if not isinstance(p.get("measurements"), list) or len(p["measurements"]) > 5000:
        raise OpError("Import must contain at most 5000 measurements")
    return WeightService(svc.db).import_rows(p["measurements"])
