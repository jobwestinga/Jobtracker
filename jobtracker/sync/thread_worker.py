"""Qt-free sync worker for the native engine host.

Same contract as :mod:`qt_worker`: network work never runs on the command
thread, each pass opens and closes its own database connection inside the
worker thread, one pass runs at a time, and a request arriving mid-pass is
remembered and run afterwards. Completion is handed to a callback that must
only enqueue a message for the engine thread; it never touches the engine's
foreground connection.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from ..core.database import Database
from .client import SyncClient
from .engine import SyncEngine, SyncResult

logger = logging.getLogger("jobtracker")


def run_pass(db_path: str, base_url: str, token: str, device_id: str) -> SyncResult:
    database = None
    try:
        database = Database(db_path)
        engine = SyncEngine(database, SyncClient(base_url, token), device_id)
        return engine.sync()
    except Exception as exc:  # noqa: BLE001 - a crash here must not stop the engine
        logger.exception("Sync thread failed")
        return SyncResult(ok=False, error=str(exc))
    finally:
        if database is not None:
            try:
                database.connection.close()
            except Exception:  # noqa: BLE001
                logger.debug("Could not close the sync connection", exc_info=True)


class ThreadSyncController:
    def __init__(self, db_path: str, device_id: str, on_done: Callable[[SyncResult], None],
                 *, run: Callable[[str, str, str, str], SyncResult] = run_pass):
        self._db_path = db_path
        self._device_id = device_id
        self._on_done = on_done
        self._run = run
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._queued_again = False
        self._credentials: tuple[str, str] = ("", "")
        self.last_result: Optional[SyncResult] = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def request(self, base_url: str, token: str) -> bool:
        """Start a pass; if one is running, remember to run again afterwards."""
        if not base_url or not token:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._queued_again = True
                self._credentials = (base_url, token)
                return False
            self._credentials = (base_url, token)
            self._thread = threading.Thread(
                target=self._work, args=(base_url, token), name="jobtracker-sync", daemon=True
            )
            self._thread.start()
            return True

    def _work(self, base_url: str, token: str) -> None:
        result = self._run(self._db_path, base_url, token, self._device_id)
        self.last_result = result
        self._on_done(result)

    def finished(self) -> bool:
        """Called on the engine thread after a completion; returns whether a
        remembered request should start now."""
        with self._lock:
            again = self._queued_again
            self._queued_again = False
            return again

    @property
    def credentials(self) -> tuple[str, str]:
        return self._credentials

    def wait_for_idle(self, seconds: float = 4.0) -> bool:
        """Bounded wait; the outbox is durable, so giving up loses nothing."""
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(seconds)
            return not thread.is_alive()
        return True
