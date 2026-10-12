"""Calendar-day body-weight history reads."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


@router.get("/api/weight")
def api_weight(device: str = Depends(require_device)) -> dict:
    from jobtracker.services.weight_service import WeightService
    with _lock:
        weights = WeightService(svc().db)
        measurements = weights.list_measurements()
        now = datetime.now()
        return {
            "measurements": measurements,
            "aliases": weights.list_aliases(),
            "today": now.date().isoformat(),
            "now": now.isoformat(timespec="seconds"),
        }
