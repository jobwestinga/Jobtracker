"""Persistent native status follows durable bookkeeping, without network I/O."""
from datetime import datetime, timedelta
import sys
import sqlite3

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtTest import QTest

from jobtracker.sync import qt_worker, settings, state
from jobtracker.sync.engine import SyncResult
from jobtracker.ui.widgets.dialog_utils import InlineDialog
from tests.support.native import _window


class FakeWorker(QObject):
    started = Signal()
    finished = Signal(object)

    def __init__(self, _path, _device, parent=None):
        super().__init__(parent)
        self.busy = False
        self.last_result = None
        self.requests = 0

    def request(self, _url, _token):
        if self.busy:
            return False
        self.requests += 1
        # Real started is emitted before QThread.start makes busy true.
        self.started.emit()
        self.busy = True
        return True

    def complete(self, result):
        self.busy = False
        self.last_result = result
        self.finished.emit(result)

    def wait_for_idle(self, _milliseconds):
        self.busy = False


@pytest.fixture
def status_window(database, monkeypatch, tmp_path):
    monkeypatch.setenv("JOBTRACKER_SYNC_TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(qt_worker, "SyncController", FakeWorker)
    windows = []

    def create(*, enabled=True, configured=True, clean=True):
        database.set_setting(settings.ENABLED_KEY, "1" if enabled else "0")
        database.set_setting(settings.SERVER_URL_KEY, "https://example.invalid" if configured else "")
        settings.write_token("synthetic-token" if configured else "")
        app, window = _window(database, monkeypatch)
        windows.append((app, window))
        if enabled and clean:
            for entry in state.pending(database.connection):
                state.drop(database.connection, entry["id"])
        if clean:
            window._sync_coordinator.refresh_status(force=True)
        return app, window

    yield create
    for app, window in windows:
        for dialog in window.findChildren(InlineDialog):
            dialog.reject()
        QTest.qWait(5)
        if window.service.active_session is not None:
            start = datetime.fromisoformat(window.service.active_session.start_time)
            window.service.stop_active_subject(start + timedelta(seconds=31))
        window.close()
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_local_status_is_stable_and_has_no_queue_polling(status_window, monkeypatch):
    _app, window = status_window(enabled=False, configured=False)
    assert "Local only" in window._sync_status.label.text()
    assert window._sync_status.action.text() == "Settings"
    assert not window._sync_coordinator._status_timer.isActive()
    queries = []
    monkeypatch.setattr(state, "pending_count", lambda _connection: queries.append(True))
    for _ in range(20):
        window._sync_coordinator.refresh_status()
    assert queries == []
    assert window.sync_now() is False


def test_disconnected_sync_keeps_durable_writes_visible(status_window):
    _app, window = status_window(configured=False)
    subject = window.service.add_subject("Offline subject", "#3B82F6", "")
    window._reload_subjects()
    assert "Sync not connected" in window._sync_status.label.text()
    assert "1 waiting" in window._sync_status.label.text()
    assert window._sync_status.action.text() == "Settings"
    assert any(row.id == subject.id for row in window.service.get_all_subjects())
    assert len(state.pending(window.service.db.connection)) == 1
    assert window.sync_now() is False


def test_started_is_visible_before_worker_busy_becomes_true(status_window):
    _app, window = status_window()
    assert window.sync_now() is True
    assert "Syncing" in window._sync_status.label.text()
    assert not window._sync_status.action.isEnabled()


def test_configured_cold_launch_does_not_incorrectly_request_restart(status_window):
    _app, window = status_window(clean=False)
    assert window._sync_controller is not None
    assert "Restart" not in window._sync_status.label.text()
    assert window._sync_status.action.text() == "Sync"


def test_unreadable_bookkeeping_settings_action_opens_settings(status_window, monkeypatch):
    _app, window = status_window()
    opened = []
    monkeypatch.setattr(window, "_open_settings", lambda: opened.append(True))
    monkeypatch.setattr(state, "pending_count", lambda _connection: (_ for _ in ()).throw(sqlite3.OperationalError("database locked")))
    window._sync_coordinator.refresh_status(force=True)
    assert window._sync_status.action.text() == "Settings"
    window._sync_status.action.click()
    assert opened == [True]
    assert window._sync_controller.requests == 0


@pytest.mark.parametrize("lost_response", [False, True])
def test_offline_or_lost_response_preserves_queue_identity_and_retry(status_window, lost_response):
    app, window = status_window()
    window.service.add_subject("Queued", "#111111", "")
    queued = state.pending(window.service.db.connection)
    window.sync_now()
    worker = window._sync_controller
    error = "Response lost after send" if lost_response else "Connection unavailable"
    worker.complete(SyncResult(ok=False, offline=True, pending=1, error=error))
    app.processEvents()
    assert "Offline" in window._sync_status.label.text() and "1 waiting" in window._sync_status.label.text()
    assert error in window._sync_status.error_label.text()
    assert state.pending(window.service.db.connection) == queued
    window._sync_status.action.click()
    assert worker.requests == 2
    assert state.pending(window.service.db.connection)[0]["op_id"] == queued[0]["op_id"]


def test_blocked_error_is_visible_accessible_plain_text_and_redacts_token(status_window):
    _app, window = status_window()
    window.service.add_subject("Queued", "#111111", "")
    worker = window._sync_controller
    window.sync_now()
    worker.complete(SyncResult(ok=False, pending=1, blocked_reason="401 <b>synthetic-token</b> denied", error="401"))
    indicator = window._sync_status
    assert "Sync blocked" in indicator.label.text()
    assert indicator.error_label.isVisible()
    assert "[redacted]" in indicator.error_label.text()
    assert "synthetic-token" not in indicator.accessibleDescription()
    assert "401" in indicator.error_label.accessibleName()


def test_success_uses_persisted_timestamp_and_does_not_hide_concurrent_local_write(status_window):
    _app, window = status_window()
    window.sync_now()
    window.service.add_subject("During sync", "#111111", "")
    state.set_value(window.service.db.connection, state.LAST_SYNC_AT, "2026-10-12T09:42:15")
    state.set_value(window.service.db.connection, state.LAST_ERROR, "")
    window._sync_controller.complete(SyncResult(ok=True, pending=0))
    assert "1 waiting" in window._sync_status.label.text()
    assert "Last sync 12 Oct 09:42" in window._sync_status.label.text()
    assert "Up to date" not in window._sync_status.label.text()
    entry = state.pending(window.service.db.connection)[0]
    state.drop(window.service.db.connection, entry["id"])
    window._sync_controller.complete(SyncResult(ok=True))
    assert "Up to date" in window._sync_status.label.text()


@pytest.mark.parametrize("timestamp", ["invalid", "", "2026-99-99T09:42:15"])
def test_corrupted_last_sync_metadata_does_not_crash_or_claim_success(status_window, timestamp):
    _app, window = status_window()
    state.set_value(window.service.db.connection, state.LAST_SYNC_AT, timestamp)
    window._sync_coordinator.refresh_status(force=True)
    assert window._sync_status.label.text() == "Ready to sync"
    assert "Last successful sync" not in window._sync_status.accessibleDescription()


def test_idle_monitor_queries_only_after_writes_and_pauses_in_background(status_window, monkeypatch):
    _app, window = status_window()
    queries = []
    original = state.pending_count
    monkeypatch.setattr(state, "pending_count", lambda connection: queries.append(True) or original(connection))
    for _ in range(20):
        window._sync_coordinator.refresh_status()
    assert queries == []
    window.service.add_subject("New write", "#111111", "")
    window._sync_coordinator.refresh_status()
    window._sync_coordinator.refresh_status()
    assert queries == [True]
    window._sync_coordinator.foreground_changed(False)
    window._reload_subjects()
    assert not window._sync_coordinator._status_timer.isActive()


def test_late_sync_result_after_closed_window_does_not_query_closed_database(status_window, monkeypatch):
    app, window = status_window()
    worker = window._sync_controller
    window.close()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    monkeypatch.setattr(state, "pending_count", lambda _connection: (_ for _ in ()).throw(AssertionError("closed read")))
    worker.complete(SyncResult(ok=True, applied=1))
    window._sync_coordinator.foreground_changed(True)
    app.processEvents()
    assert errors == []
    assert not window._sync_coordinator._status_timer.isActive()


def test_local_edits_push_promptly_with_one_debounced_request(status_window, monkeypatch):
    # Startup, focus and periodic sync call the window adapter and can occupy the
    # worker first. Isolate them so the count measures only the push timer.
    monkeypatch.setattr("jobtracker.ui.app.MainWindow.sync_now", lambda _window: False)
    _app, window = status_window()
    coordinator, worker = window._sync_coordinator, window._sync_controller
    coordinator._push_timer.setInterval(20)
    before = worker.requests
    for name in ("First", "Second", "Third"):
        window.service.add_subject(name, "#111111", "")
        coordinator.refresh_status()
    assert worker.requests == before
    # A loaded CI runner can fire the 20 ms timer late: wait for the push, then
    # prove the debounce produced exactly one request.
    for _ in range(200):
        if worker.requests > before:
            break
        QTest.qWait(10)
    QTest.qWait(60)
    assert worker.requests == before + 1


def test_clean_pass_with_remaining_work_retries_only_a_bounded_number_of_times(status_window, monkeypatch):
    # Startup, focus and periodic sync call the window adapter. Isolate those
    # triggers so the request count measures only the coordinator's push timer.
    monkeypatch.setattr("jobtracker.ui.app.MainWindow.sync_now", lambda _window: False)
    _app, window = status_window()
    coordinator, worker = window._sync_coordinator, window._sync_controller
    coordinator._push_timer.setInterval(5)
    window.service.add_subject("Waiting", "#111111", "")
    coordinator.refresh_status()
    for _ in range(8):
        QTest.qWait(40)
        if worker.busy:
            # A postponed pull leaves the edit queued after a successful pass.
            worker.complete(SyncResult(ok=True, pending=1))
    assert worker.requests == 1 + 3
    # New local work earns a fresh prompt push; an offline pass does not loop.
    window.service.add_subject("Another", "#111111", "")
    coordinator.refresh_status()
    QTest.qWait(40)
    assert worker.requests == 5
    worker.complete(SyncResult(ok=False, offline=True, pending=2))
    QTest.qWait(40)
    assert worker.requests == 5
    assert len(state.pending(window.service.db.connection)) == 2
