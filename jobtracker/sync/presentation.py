"""Persistent sync status wording shared by the Qt window and native engine.

Pure presentation over values the caller already read: no database, network or
Qt access. The token is used only to redact itself from error text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core import timeutils


@dataclass(frozen=True)
class SyncStatusText:
    title: str
    detail: str
    action: str
    error: str


def describe_status(
    *, enabled: bool, url: str, token: str, pending: int, last_sync_at: str,
    last_error: str, result, busy: bool, connected: bool, status_error: str = "",
) -> SyncStatusText:
    pending_text = f" · {pending} waiting" if pending else ""
    stamp = ""
    if last_sync_at:
        try:
            parsed = timeutils.parse_iso(last_sync_at)
            if parsed is not None:
                stamp = parsed.strftime(" · Last sync %d %b %H:%M")
        except (TypeError, ValueError):
            pass
    error: Optional[str] = status_error
    if error:
        title, detail, action = f"Sync status unavailable{pending_text}", "Sync bookkeeping could not be read. Open Settings to retry.", "Settings"
    elif not enabled:
        title, detail, action = f"Local only · Sync off{pending_text}", "Changes are saved on this Mac. Open Settings to enable sync.", "Settings"
    elif not url or not token:
        title, detail, action = f"Sync not connected{pending_text}", "Set the server address and access token in Settings.", "Settings"
    elif not connected:
        title, detail, action = f"Restart to enable sync{pending_text}", "Sync preferences changed. Restart JobTracker when the timer is stopped to connect.", "Settings"
    elif busy:
        title, detail, action = f"Syncing{pending_text}{stamp}", "Synchronizing with your server. Your local data remains available.", "Sync"
    elif result is not None and result.offline:
        title, detail, action = f"Offline{pending_text}{stamp}", "Changes remain saved here and retry automatically.", "Retry"
        error = result.error or "The server is unreachable."
    elif result is not None and not result.ok:
        title, detail, action = f"Sync blocked{pending_text}{stamp}", "Queued changes remain on this Mac. Open Settings if retry does not resolve the issue.", "Retry"
        error = result.blocked_reason or result.error or "Synchronization did not finish."
    elif last_error and result is None:
        title, detail, action = f"Sync needs attention{pending_text}{stamp}", "The last sync did not finish. Retry or open Settings.", "Retry"
        error = last_error
    else:
        title = f"Changes waiting{pending_text}{stamp}" if pending else f"Up to date{stamp}" if stamp else "Ready to sync"
        detail, action = "Sync retries automatically, on returning to the app, and when you press Sync.", "Sync"
    if error and token:
        error = str(error).replace(token, "[redacted]")
    if error:
        detail += f"\n{error}"
    if stamp:
        detail += f"\nLast successful sync: {last_sync_at}"
    return SyncStatusText(title, detail, action, error or "")
