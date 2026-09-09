"""HTTP transport to the JobTracker server.

Deliberately built on ``urllib`` from the standard library rather than a nicer
HTTP package: the desktop app's only third-party dependency is PySide6, and
keeping it that way means nothing new has to be bundled into the signed .app
(``JobTracker.spec`` even excludes QtNetwork on purpose).

Every method either returns parsed JSON or raises :class:`SyncError`. Callers
distinguish "the server said no" (``status`` set — a real refusal worth showing
the user) from "could not reach the server" (``status`` None — normal offline
condition, keep the outbox and try later).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger("jobtracker")

DEFAULT_TIMEOUT = 15.0

# Trust roots, in the order we prefer them.
#
# This matters more than it looks: the python.org framework build (which is what
# PyInstaller freezes into JobTracker.app) does NOT read the macOS system
# keychain. It looks in its own `etc/openssl/` directory, which is empty unless
# someone ran "Install Certificates.command". The result is that a perfectly
# valid Let's Encrypt certificate fails with CERTIFICATE_VERIFY_FAILED while
# `curl` on the same machine is happy.
#
# `/etc/ssl/cert.pem` is part of macOS itself, so it is present for the frozen
# app and for a dev checkout alike. certifi is used if it happens to be
# installed. Verification is never disabled.
_CA_CANDIDATES = ("/etc/ssl/cert.pem",)


@lru_cache(maxsize=1)
def build_ssl_context() -> ssl.SSLContext:
    for path in _CA_CANDIDATES:
        if os.path.exists(path):
            try:
                context = ssl.create_default_context(cafile=path)
                if context.get_ca_certs():
                    logger.debug("TLS roots loaded from %s", path)
                    return context
            except (OSError, ssl.SSLError):
                logger.warning("Could not load CA bundle %s", path, exc_info=True)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi is optional
        pass
    logger.warning("Falling back to Python's default CA store; TLS may fail")
    return ssl.create_default_context()


class SyncError(Exception):
    """A request failed. ``status`` is None when the server was unreachable."""

    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body

    @property
    def offline(self) -> bool:
        """True when this was a transport failure rather than a refusal."""
        return self.status is None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.message} (status={self.status})" if self.status else self.message


class SyncClient:
    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    # ── plumbing ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        if not self.base_url:
            raise SyncError("no server configured")
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            context = build_ssl_context() if url.startswith("https://") else None
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=context
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail", raw)
            except ValueError:
                parsed, detail = None, raw
            # A refusal, not an outage: the caller should surface this.
            raise SyncError(str(detail)[:300], status=exc.code, body=parsed) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            # Unreachable. Normal while offline — never treated as data loss.
            raise SyncError(f"cannot reach server: {exc}") from exc

        try:
            return json.loads(body) if body else {}
        except ValueError as exc:
            raise SyncError("server returned invalid JSON") from exc

    # ── endpoints ───────────────────────────────────────────────────────
    def health(self) -> dict:
        return self._request("GET", "/health")

    def snapshot(self) -> dict:
        return self._request("GET", "/api/snapshot")

    def pull(self, since: int, limit: int = 5000) -> dict:
        query = urllib.parse.urlencode({"since": int(since), "limit": int(limit)})
        return self._request("GET", f"/sync/pull?{query}")

    def integrity(self, deep: bool = False) -> dict:
        return self._request("GET", f"/sync/integrity?deep={'1' if deep else '0'}")

    def send_ops(self, ops: list[dict], device_id: str = "") -> dict:
        payload = {
            "ops": [
                {
                    "op_id": item["op_id"],
                    "op": item["op"],
                    "params": item["params"],
                    "device_id": device_id or None,
                }
                for item in ops
            ]
        }
        return self._request("POST", "/ops", payload)

    def active(self) -> dict:
        return self._request("GET", "/api/active")
