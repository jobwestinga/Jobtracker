"""Talking to the user's own JobTracker server.

The desktop app keeps its SQLite file as a **mirror**: fast local reads, and it
still opens with the server down. The server is the only writer of record, so
there is no two-way merge anywhere in here.

Nothing in this package is imported by the app unless sync is switched on.
"""

from .client import SyncClient, SyncError
from .engine import SyncEngine, SyncResult

__all__ = ["SyncClient", "SyncError", "SyncEngine", "SyncResult"]
