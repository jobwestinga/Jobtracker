"""Bearer authentication shared by each route group.

Keep one callable identity so FastAPI dependency overrides work across routers.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from . import auth


def require_device(authorization: str = Header(default="")) -> str:
    """Bearer-token auth. Returns the calling device's name."""
    prefix = "bearer "
    token = (
        authorization[len(prefix):].strip()
        if authorization.lower().startswith(prefix)
        else ""
    )
    device = auth.device_for_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return device
