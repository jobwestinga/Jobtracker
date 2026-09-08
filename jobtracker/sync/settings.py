"""Where the sync configuration lives, and why the token is not in the database.

The server URL and the on/off switch are ordinary device-local settings. The API
token is deliberately **not**: `export_data()` copies the whole settings table
into the backup JSON, so a token stored there would ride along into every backup
the user exports and possibly shares. It lives in its own 0600 file instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..core.config import DATA_DIR

logger = logging.getLogger("jobtracker")

ENABLED_KEY = "sync_enabled"
SERVER_URL_KEY = "sync_server_url"

# Device-local settings, never shared (they are not in SYNCED_SETTING_KEYS).
DEFAULT_ENABLED = "0"


def token_path() -> Path:
    return Path(
        os.environ.get("JOBTRACKER_SYNC_TOKEN_PATH", DATA_DIR / "sync_token")
    ).expanduser()


def read_token() -> str:
    path = token_path()
    try:
        return path.read_text().strip() if path.exists() else ""
    except OSError:
        logger.exception("Could not read the sync token")
        return ""


def write_token(token: str) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not token.strip():
        path.unlink(missing_ok=True)
        return
    path.write_text(token.strip())
    path.chmod(0o600)


def is_enabled(service) -> bool:
    return service.get_setting(ENABLED_KEY, DEFAULT_ENABLED) == "1"


def set_enabled(service, enabled: bool) -> None:
    service.set_setting(ENABLED_KEY, "1" if enabled else "0")


def server_url(service) -> str:
    return (service.get_setting(SERVER_URL_KEY, "") or "").strip().rstrip("/")


def set_server_url(service, url: str) -> None:
    service.set_setting(SERVER_URL_KEY, (url or "").strip().rstrip("/"))


def is_configured(service) -> bool:
    return bool(server_url(service)) and bool(read_token())


def describe(service) -> Optional[str]:
    """Why sync cannot run, or None when it can."""
    if not is_enabled(service):
        return "Sync is off"
    if not server_url(service):
        return "No server address set"
    if not read_token():
        return "No access token set"
    return None
