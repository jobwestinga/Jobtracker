"""Background sync wiring for an injected native window; no new database owner."""
from __future__ import annotations
import logging
from PySide6.QtCore import QTimer
from ...sync import settings as sync_settings
logger = logging.getLogger("jobtracker")

class WindowSyncController:
    def __init__(self, window):
        self.window = window

    def _setup_sync(self) -> None:
        window = self.window
        """Start the background sync thread, if sync is switched on.

        Nothing here is on a UI code path: if the server is unreachable the app
        simply carries on with its local mirror.
        """
        if not window._sync_on or not sync_settings.is_configured(window.service):
            return
        from ...sync.qt_worker import SyncController

        window._sync_controller = SyncController(
            window.service.db.db_path, window.service.device_id, parent=window
        )
        window._sync_controller.finished.connect(window._on_sync_finished)

        # On launch, then on a slow timer. Focus and quit trigger it too.
        QTimer.singleShot(1200, window.sync_now)
        window._sync_timer = QTimer(window)
        window._sync_timer.setInterval(5 * 60_000)
        window._sync_timer.timeout.connect(window.sync_now)
        window._sync_timer.start()

    def sync_now(self) -> bool:
        window = self.window
        if window._sync_controller is None:
            return False
        return window._sync_controller.request(
            sync_settings.server_url(window.service), sync_settings.read_token()
        )

    def _on_sync_finished(self, result) -> None:
        window = self.window
        if result.applied or result.deleted or result.repaired:
            # The mirror changed underneath the widgets, so redraw from it.
            window.service._adopt_open_session()
            window._reload()
        if not result.ok and not result.offline:
            logger.warning("Sync problem: %s", result.error)

