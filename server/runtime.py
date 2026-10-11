"""One process-wide service and lock shared by all authenticated route groups.

The composition lifespan installs the service once. Reads and ordered write
batches hold the same lock before accessing the shared SQLite connection.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import HTTPException

from jobtracker.services.tracker_service import TrackerService

_lock = threading.Lock()
_state: dict[str, Any] = {"db": None, "svc": None}


def svc() -> TrackerService:
    service = _state.get("svc")
    if service is None:
        raise HTTPException(status_code=503, detail="server not ready")
    return service
