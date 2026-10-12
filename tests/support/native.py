"""Offscreen native application builders shared by widget integration tests."""
from datetime import datetime, timedelta
from PySide6.QtWidgets import QApplication
from jobtracker.services.tracker_service import TrackerService
from jobtracker.ui import app as app_module

def _application():
    return QApplication.instance() or QApplication([])


def _window(database, monkeypatch):
    qt_app = _application()
    monkeypatch.setattr(app_module, "TrackerService", lambda **kwargs: TrackerService(database, **kwargs))
    window = app_module.MainWindow(qt_app)
    window.show()
    qt_app.processEvents()
    return qt_app, window


def _agenda_canvas_with(sessions, day_keys):
    from jobtracker.ui.widgets.agenda_view import _AgendaCanvas

    _application()
    canvas = _AgendaCanvas()
    canvas.resize(700, 420)
    canvas.set_data(sessions, day_keys, hour_start=6, hour_end=23)
    canvas.grab()  # paint pass builds the hit rectangles
    return canvas


def _day_with_two_sessions(service):
    from jobtracker.core import timeutils

    subject_a = service.add_subject("Physics", "#3B82F6", "")
    subject_b = service.add_subject("Maths", "#22C55E", "")
    base = (datetime.now() - timedelta(days=2)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    first = service.add_session(subject_a.id, base, base + timedelta(hours=1))
    second = service.add_session(
        subject_b.id,
        base + timedelta(hours=5),
        base + timedelta(hours=7),
    )
    return timeutils.logical_day(base), subject_a, subject_b, first, second

