"""Running sync off the UI thread.

This is the app's only background thread, so it is deliberately small and boring:

* **The UI thread never touches the network.** Every request happens here, so a
  slow or unreachable server can never freeze a click.
* **The worker opens its own database connection.** Sharing one sqlite3
  connection across threads is not safe; WAL plus ``busy_timeout`` (see
  ``Database.__init__``) lets the two connections work at the same time.
* **One sync at a time.** A request arriving while a pass is running is
  remembered and run afterwards, rather than starting a second overlapping pass.
* **Failure is never fatal.** A sync that cannot finish leaves the outbox intact
  and reports itself in the UI; the app keeps working exactly as it does offline.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..core.database import Database
from .client import SyncClient
from .engine import SyncEngine, SyncResult

logger = logging.getLogger("jobtracker")


class _SyncTask(QThread):
    """One sync pass, on its own connection."""

    done = Signal(object)

    def __init__(self, db_path: str, base_url: str, token: str, device_id: str, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._base_url = base_url
        self._token = token
        self._device_id = device_id

    def run(self) -> None:  # noqa: D102 - QThread entry point
        database = None
        try:
            database = Database(self._db_path)
            engine = SyncEngine(
                database, SyncClient(self._base_url, self._token), self._device_id
            )
            result = engine.sync()
        except Exception as exc:  # noqa: BLE001 - a crash here must not kill the app
            logger.exception("Sync thread failed")
            result = SyncResult(ok=False, error=str(exc))
        finally:
            if database is not None:
                try:
                    database.connection.close()
                except Exception:  # noqa: BLE001
                    logger.debug("Could not close the sync connection", exc_info=True)
        self.done.emit(result)


class SyncController(QObject):
    """Owns the worker thread and tells the UI what happened."""

    started = Signal()
    finished = Signal(object)  # SyncResult

    def __init__(self, db_path: str, device_id: str, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._device_id = device_id
        self._task: Optional[_SyncTask] = None
        self._queued_again = False
        self.last_result: Optional[SyncResult] = None

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def request(self, base_url: str, token: str) -> bool:
        """Start a sync. Returns False if one is already running (and remembers
        to run again once it finishes, so a trigger is never simply dropped)."""
        if not base_url or not token:
            return False
        if self.busy:
            self._queued_again = True
            return False

        self._base_url, self._token = base_url, token
        task = _SyncTask(self._db_path, base_url, token, self._device_id, parent=self)
        task.done.connect(self._on_done)
        task.finished.connect(task.deleteLater)
        self._task = task
        self.started.emit()
        task.start()
        return True

    def _on_done(self, result: SyncResult) -> None:
        self.last_result = result
        self._task = None
        self.finished.emit(result)
        if self._queued_again:
            self._queued_again = False
            self.request(self._base_url, self._token)

    def wait_for_idle(self, milliseconds: int = 4000) -> None:
        """Let an in-flight sync finish, e.g. while quitting.

        Bounded on purpose: the outbox is durable, so waiting forever to push a
        change buys nothing over sending it at next launch.
        """
        task = self._task
        if task is not None and task.isRunning():
            task.wait(milliseconds)
