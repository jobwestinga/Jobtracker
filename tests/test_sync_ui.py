"""How sync is wired into the window and the Settings dialog.

The central guarantee: with sync switched off the app behaves exactly as it did
before this feature existed — no outbox, no thread, no network.
"""

import pytest
from PySide6.QtWidgets import QApplication

from jobtracker.services.tracker_service import TrackerService
from jobtracker.sync import settings as sync_settings
from jobtracker.sync import state
from jobtracker.sync.service import SyncedTrackerService
from jobtracker.ui import app as app_module
from jobtracker.ui.widgets.settings_dialog import SettingsDialog


def _application():
    return QApplication.instance() or QApplication([])


def _window(database, monkeypatch):
    qt_app = _application()
    monkeypatch.setattr(app_module, "TrackerService", lambda: TrackerService(database))
    window = app_module.MainWindow(qt_app)
    window.show()
    qt_app.processEvents()
    return qt_app, window


@pytest.fixture(autouse=True)
def isolated_token(tmp_path, monkeypatch):
    """Never touch the real token file from a test."""
    monkeypatch.setenv("JOBTRACKER_SYNC_TOKEN_PATH", str(tmp_path / "sync_token"))


# ── sync off is the old app, exactly ────────────────────────────────────


def test_sync_is_off_by_default(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._sync_on is False
        assert window._sync_controller is None
        assert type(window.service) is TrackerService
    finally:
        window.close()


def test_with_sync_off_nothing_is_queued(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Physics", "#3B82F6", "")
        window.service.add_todo_task("A goal", "", None)
        window.service.archive_subject(subject.id)

        cur = database.connection.cursor()
        tables = [
            r["name"]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_outbox'"
            )
        ]
        if tables:  # table may exist from an earlier enable; it must be empty
            assert state.pending_count(database.connection) == 0
    finally:
        window.close()


def test_sync_now_is_a_no_op_when_off(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window.sync_now() is False
    finally:
        window.close()


# ── sync on ─────────────────────────────────────────────────────────────


def test_enabling_sync_upgrades_the_service_and_routes_writes(database, monkeypatch):
    database.set_setting(sync_settings.ENABLED_KEY, "1")
    sync_settings.set_server_url(TrackerService(database), "https://example.invalid:8443")
    sync_settings.write_token("test-token")

    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._sync_on is True
        assert isinstance(window.service, SyncedTrackerService)
        # Same database — upgrading the service must not open a second one.
        assert window.service.db is database

        window.service.add_subject("Physics", "#3B82F6", "")
        assert "add_subject" in [
            e["op"] for e in state.pending(database.connection)
        ]
    finally:
        window.close()


def test_recovery_does_not_run_twice_when_the_service_is_upgraded(database, monkeypatch):
    """The plain service already recovered; the upgrade must not close sessions
    a second time."""
    database.set_setting(sync_settings.ENABLED_KEY, "1")
    sync_settings.write_token("test-token")
    svc = TrackerService(database)
    subject = svc.add_subject("Physics", "#3B82F6", "")
    svc.start_subject(subject.id)
    open_before = len(database.get_open_sessions())

    qt_app, window = _window(database, monkeypatch)
    try:
        assert len(database.get_open_sessions()) == open_before
        assert window.service.active_session is not None
    finally:
        window.service.stop_active_subject()
        window.close()


# ── the Settings dialog ─────────────────────────────────────────────────


def test_settings_dialog_shows_sync_controls(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        dialog = SettingsDialog(window, service=window.service)
        assert dialog.sync_enabled_check.isChecked() is False
        assert dialog.sync_url_input.text() == ""
        assert "off" in dialog.sync_status_lbl.text().lower()
        dialog.deleteLater()
    finally:
        window.close()


def test_saving_settings_persists_sync_configuration(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        dialog = SettingsDialog(window, service=window.service)
        dialog.sync_enabled_check.setChecked(True)
        dialog.sync_url_input.setText("https://example.invalid:8443/")
        dialog.sync_token_input.setText("a-secret-token")
        dialog.get_settings()

        assert sync_settings.is_enabled(window.service) is True
        # Trailing slash trimmed so paths are not doubled up.
        assert sync_settings.server_url(window.service) == "https://example.invalid:8443"
        assert sync_settings.read_token() == "a-secret-token"
        dialog.deleteLater()
    finally:
        window.close()


def test_the_token_is_never_rendered_back_into_the_dialog(database, monkeypatch):
    sync_settings.write_token("super-secret")
    qt_app, window = _window(database, monkeypatch)
    try:
        dialog = SettingsDialog(window, service=window.service)
        assert dialog.sync_token_input.text() == ""
        assert "super-secret" not in dialog.sync_token_input.placeholderText()
        dialog.deleteLater()
    finally:
        window.close()


def test_blank_token_field_keeps_the_existing_token(database, monkeypatch):
    """Editing the server address must not silently wipe the credentials."""
    sync_settings.write_token("keep-me")
    qt_app, window = _window(database, monkeypatch)
    try:
        dialog = SettingsDialog(window, service=window.service)
        dialog.sync_url_input.setText("https://elsewhere.invalid:8443")
        dialog.get_settings()

        assert sync_settings.read_token() == "keep-me"
        dialog.deleteLater()
    finally:
        window.close()


def test_the_token_never_lands_in_a_backup(database, monkeypatch):
    """export_data() copies the settings table, so a token stored there would
    ride along into every exported backup."""
    sync_settings.write_token("must-not-be-exported")
    qt_app, window = _window(database, monkeypatch)
    try:
        payload = window.service.export_data()
        assert "must-not-be-exported" not in str(payload)
        keys = {s["key"] for s in payload["settings"]}
        assert not any("token" in k for k in keys)
    finally:
        window.close()
