"""Background sync wiring for an injected native window; no new database owner."""
from __future__ import annotations
import logging
import sqlite3
from PySide6.QtCore import QTimer
from ...sync.presentation import describe_status
from ...sync import settings as sync_settings
from ...sync import state
logger = logging.getLogger("jobtracker")

PUSH_DELAY_MS = 1500
# A pass can finish cleanly with work still queued, for example when an edit
# arrives mid-pass and the pull is postponed. Retry that a few times, then
# leave it to the regular triggers rather than polling the server.
MAX_FOLLOW_UPS = 3

class WindowSyncController:
    def __init__(self, window):
        self.window = window
        self._total_changes = None
        self._pending = 0
        self._last_sync_at = ""
        self._last_error = ""
        self._result = None
        self._status_error = ""
        self._config = None
        self._displayed_action = "Settings"
        self._syncing = False
        self._foreground = True
        self._closed = False
        self._status_timer = QTimer(window)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self.refresh_status)
        # New local work is pushed shortly after it is queued, instead of
        # waiting for focus or the five-minute timer. One debounced request
        # covers a burst of edits; the worker still runs one pass at a time.
        self._pending_read = False
        self._follow_ups = 0
        self._push_timer = QTimer(window)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(PUSH_DELAY_MS)
        self._push_timer.timeout.connect(self.sync_now)

    def _setup_sync(self) -> None:
        """Start the background sync thread, if sync is switched on.

        Nothing here is on a UI code path: if the server is unreachable the app
        simply carries on with its local mirror.
        """
        window = self.window
        self.refresh_status(force=True, configuration=True)
        if window._sync_on:
            self._status_timer.start()
        if not window._sync_on or not sync_settings.is_configured(window.service):
            return
        from ...sync.qt_worker import SyncController

        window._sync_controller = SyncController(
            window.service.db.db_path, window.service.device_id, parent=window
        )
        window._sync_controller.started.connect(self._on_sync_started)
        window._sync_controller.finished.connect(window._on_sync_finished)
        self.refresh_status()

        # On launch, then on a slow timer. Focus and quit trigger it too.
        QTimer.singleShot(1200, window, window.sync_now)
        window._sync_timer = QTimer(window)
        window._sync_timer.setInterval(5 * 60_000)
        window._sync_timer.timeout.connect(window.sync_now)
        window._sync_timer.start()

    def sync_now(self) -> bool:
        window = self.window
        if self._closed:
            return False
        self.refresh_status(configuration=True)
        if window._sync_controller is None or not self._config[0] or not all(self._config[1:]):
            return False
        return window._sync_controller.request(
            sync_settings.server_url(window.service), sync_settings.read_token()
        )

    def _on_sync_finished(self, result) -> None:
        window = self.window
        self._syncing = False
        if self._closed:
            return
        self._result = result
        self.refresh_status(force=True)
        if result.ok and self._pending and self._follow_ups < MAX_FOLLOW_UPS:
            self._follow_ups += 1
            self._schedule_push()
        elif not self._pending:
            self._follow_ups = 0
        if result.applied or result.deleted or result.repaired:
            # The mirror changed underneath the widgets, so redraw from it.
            window.service._adopt_open_session()
            window._reload()
        if not result.ok and not result.offline:
            logger.warning("Sync problem: %s", result.error)

    def _on_sync_started(self) -> None:
        self._syncing = True
        self.refresh_status(force=True)

    def foreground_changed(self, active: bool) -> None:
        if self._closed:
            return
        self._foreground = active
        if active and self.window._sync_on:
            self._status_timer.start()
            self.refresh_status()
        else:
            self._status_timer.stop()

    def close(self) -> None:
        self._closed = True
        self._status_timer.stop()
        self._push_timer.stop()

    def _schedule_push(self) -> None:
        if not self._closed and self.window._sync_controller is not None:
            self._push_timer.start()

    def refresh_status(self, *, force=False, configuration=False) -> None:
        """Read only after local mutations or a worker event, never each tick.

        ``total_changes`` is an in-memory counter for the UI's connection.
        Worker acknowledgments use another connection, so results force a read.
        Widgets receive only presentation data; the existing sync coordinator
        owns these read-only bookkeeping calls alongside its worker wiring.
        """
        window = self.window
        indicator = getattr(window, "_sync_status", None)
        if indicator is None or self._closed:
            return
        if configuration or self._config is None:
            self._config = (
                sync_settings.is_enabled(window.service),
                sync_settings.server_url(window.service),
                sync_settings.read_token(),
            )
        connection = window.service.db.connection
        if force or connection.total_changes != self._total_changes:
            try:
                previous = self._pending if self._pending_read else None
                self._pending = state.pending_count(connection)
                self._pending_read = True
                if previous is not None and self._pending > previous:
                    self._follow_ups = 0
                    self._schedule_push()
                self._last_sync_at = state.get(connection, state.LAST_SYNC_AT)
                self._last_error = state.get(connection, state.LAST_ERROR)
                self._status_error = ""
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    self._status_error = "Sync status could not be read. Open Settings to retry."
                # A purely local database intentionally has no sync tables.
            except sqlite3.DatabaseError:
                self._status_error = "Sync status could not be read. Open Settings to retry."
            self._total_changes = connection.total_changes
        if window._sync_on and self._foreground and not self._status_timer.isActive() and window.isVisible():
            self._status_timer.start()
        self._paint_status()

    def _paint_status(self) -> None:
        window = self.window
        enabled, url, token = self._config
        busy = self._syncing or bool(window._sync_controller and window._sync_controller.busy)
        text = describe_status(
            enabled=enabled, url=url, token=token, pending=self._pending,
            last_sync_at=self._last_sync_at, last_error=self._last_error,
            result=self._result, busy=busy,
            connected=bool(window._sync_on and window._sync_controller is not None),
            status_error=self._status_error,
        )
        self._displayed_action = text.action
        window._sync_status.set_status(text.title, text.detail, action=text.action, busy=busy and enabled, error=text.error)

    def status_action(self) -> None:
        if self._displayed_action == "Settings":
            self.window._open_settings()
        else:
            self.sync_now()

