"""Settings operation handlers; transaction and receipt policy lives in server.ops."""

from __future__ import annotations

from jobtracker.core import sync_policy
from .common import (
    OpError,
)

def set_setting(svc, p):
    key = p["key"]
    if key not in sync_policy.SYNCED_SETTING_KEYS:
        # Theme, graph range and the like are per-machine on purpose: the phone
        # does not get to dictate the Mac's window state.
        raise OpError(f"{key} is a device-local setting and is not shared")
    svc.set_setting(key, str(p["value"]))
    return {"key": key, "value": svc.get_setting(key)}
