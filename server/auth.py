"""Per-device bearer tokens.

Single-user service, but it holds a complete record of when the user works, so
it gets real authentication rather than obscurity — even behind Tailscale, which
is the actual first line of defence.

Tokens are stored **hashed**: the file cannot be turned back into a working
credential if it leaks. Each device gets its own token so one can be revoked
without disturbing the other.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger("jobtracker.server")

TOKEN_BYTES = 32


def token_store_path() -> Path:
    return Path(
        os.environ.get(
            "JOBTRACKER_TOKENS_PATH",
            Path.home() / ".config" / "jobtracker" / "tokens.json",
        )
    ).expanduser()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_tokens() -> list[dict]:
    path = token_store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        logger.exception("Token store unreadable: %s", path)
        return []
    return data if isinstance(data, list) else []


def save_tokens(tokens: list[dict]) -> None:
    path = token_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2))
    path.chmod(0o600)


def issue_token(name: str) -> str:
    """Mint a token for a device. The plaintext is returned once and never stored."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    tokens = load_tokens()
    tokens = [t for t in tokens if t.get("name") != name]
    tokens.append({"name": name, "sha256": hash_token(token), "revoked": False})
    save_tokens(tokens)
    logger.info("Issued API token for device %r", name)
    return token


def revoke_token(name: str) -> bool:
    tokens = load_tokens()
    found = False
    for entry in tokens:
        if entry.get("name") == name:
            entry["revoked"] = True
            found = True
    if found:
        save_tokens(tokens)
    return found


def device_for_token(token: str) -> str | None:
    """The device name this token belongs to, or None.

    Every candidate is compared with ``compare_digest`` and the loop always runs
    to completion, so response time does not reveal how much of a guess matched.
    """
    if not token:
        return None
    candidate = hash_token(token)
    match: str | None = None
    for entry in load_tokens():
        if entry.get("revoked"):
            continue
        stored = str(entry.get("sha256", ""))
        if secrets.compare_digest(candidate, stored):
            match = str(entry.get("name"))
    return match
