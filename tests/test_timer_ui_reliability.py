"""Desktop wiring regressions for recovery prompts and remote timer changes."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QDialog

from jobtracker.services.tracker_service import TrackerService
from jobtracker.ui import app as app_module


def _window(database, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        app_module, "TrackerService", lambda **kwargs: TrackerService(database, **kwargs)
    )
    monkeypatch.setattr(app_module, "write_auto_backup", lambda *args: None)
    window = app_module.MainWindow(application)
    return application, window


def _close(window, application):
    window._graph_live_timer.stop()
    window._heartbeat_timer.stop()
    if window.service.active_session is not None:
        start = datetime.fromisoformat(window.service.active_session.start_time)
        window.service.stop_active_subject(max(datetime.now(), start + timedelta(seconds=31)))
    window.close()
    application.processEvents()


def test_sync_refresh_clears_remote_stopped_timer(database, monkeypatch):
    application, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Work", "#123456", "")
        start = datetime.now() - timedelta(minutes=75)
        window.service.start_subject(subject.id, start)
        session_id = window.service.active_session.id
        database.stop_session(session_id, datetime.now(), 4500)
        window._on_sync_finished(SimpleNamespace(applied=1, deleted=0, repaired=False, ok=True))
        assert window.service.active_session is None
        assert database.get_session(session_id).duration_seconds == 4500
    finally:
        _close(window, application)


def test_pending_recovery_prompt_preserves_old_heartbeat(database, monkeypatch):
    dialogs = []
    monkeypatch.setattr(app_module, "open_dialog", lambda dialog, callback: dialogs.append(dialog))
    service = TrackerService(database)
    subject = service.add_subject("Recovered", "#123456", "")
    start = datetime.now() - timedelta(hours=2)
    service.start_subject(subject.id, start)
    application, window = _window(database, monkeypatch)
    try:
        window._maybe_prompt_recovery()
        assert dialogs
        last_active = database.get_session(window.service.active_session.id).last_active_at
        window._heartbeat()
        assert database.get_session(window.service.active_session.id).last_active_at == last_active
    finally:
        for dialog in dialogs:
            dialog.deleteLater()
        _close(window, application)


def test_recovery_acceptance_cannot_close_a_newer_timer(database, monkeypatch):
    dialogs = []
    monkeypatch.setattr(app_module, "open_dialog", lambda dialog, callback: dialogs.append(dialog))
    service = TrackerService(database)
    subject = service.add_subject("Recovered", "#123456", "")
    start = datetime.now() - timedelta(hours=2)
    service.start_subject(subject.id, start)
    application, window = _window(database, monkeypatch)
    try:
        window._maybe_prompt_recovery()
        old_id = window.service.active_session.id
        window.service.stop_active_subject(start + timedelta(minutes=75))
        window.service.start_subject(subject.id)
        new_id = window.service.active_session.id
        window._finish_recovery(QDialog.Accepted, dialogs[0])
        assert database.get_session(old_id).duration_seconds == 4500
        assert database.get_session(new_id).end_time is None
        assert window.service.active_session.id == new_id
    finally:
        for dialog in dialogs:
            dialog.deleteLater()
        _close(window, application)


def test_invalid_saved_agenda_hours_do_not_prevent_launch(database, monkeypatch):
    database.set_setting("graph_hour_start", "garbage")
    database.set_setting("graph_hour_end", "999999999999999999999999999999")
    application, window = _window(database, monkeypatch)
    try:
        assert window._graph_hour_start == 6
        assert window._graph_hour_end == 27
        window._switch_page(2)
    finally:
        _close(window, application)

