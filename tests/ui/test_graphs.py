"""Offscreen native graphs regressions on isolated databases."""
from datetime import (datetime, timedelta)
from PySide6.QtCore import (QPoint, Qt)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QMessageBox)
from jobtracker.ui.widgets.graph_settings_dialog import (GraphSettingsDialog)
from jobtracker.ui.widgets.graph_settings_dialog import (RANGE_OPTIONS)
from tests.support.native import (_application, _window, _agenda_canvas_with)

def test_graph_ranges_are_calendar_presets_with_custom_still_available():
    qt_app = _application()
    assert RANGE_OPTIONS == [
        ("Weeks", "weeks"),
        ("Months", "months"),
        ("Year", "year"),
        ("All Time", "all"),
    ]
    dialog = GraphSettingsDialog()
    dialog._select_mode(0)
    assert [button.text() for button in dialog.range_btns] == [
        "Weeks", "Months", "Year", "All Time",
    ]
    assert not dialog.custom_check.isHidden()
    dialog.close()


def test_bar_chart_hover_geometry_and_day_click():
    from jobtracker.ui.widgets.graphs_view import WorkGraphWidget

    qt_app = _application()
    view = WorkGraphWidget()
    view.resize(800, 500)
    view.show()
    qt_app.processEvents()

    data = [
        {
            "date": "2026-06-20",
            "total_seconds": 3600,
            "segments": [
                {
                    "subject_id": 1,
                    "subject_name": "Physics",
                    "color": "#3B82F6",
                    "seconds": 3600,
                }
            ],
        },
        {"date": "2026-06-21", "total_seconds": 0, "segments": []},
    ]
    view.set_data(data, fit_width=True, grouping="daily")
    qt_app.processEvents()

    canvas = view._canvas
    chart = canvas._chart_rect()
    bar_width, gap = canvas._bar_layout()
    first_x = chart.left() + bar_width / 2
    second_x = chart.left() + bar_width + gap + bar_width / 2
    y = chart.center().y()
    assert canvas._bar_index_at(first_x, y) == 0
    assert canvas._bar_index_at(second_x, y) == 1
    assert canvas._bar_index_at(chart.left() - 10, y) is None
    assert canvas._bar_index_at(first_x, chart.top() - 10) is None

    html = canvas._bar_html(data[0])
    assert "Physics" in html and "1.0" in html

    clicked: list[str] = []
    view.day_clicked.connect(clicked.append)
    QTest.mouseClick(canvas, Qt.LeftButton, pos=QPoint(int(first_x), int(y)))
    assert clicked == ["2026-06-20"]

    # Weekly/monthly buckets do not open a single-day inspector.
    view.set_data(data, fit_width=True, grouping="weekly")
    qt_app.processEvents()
    QTest.mouseClick(canvas, Qt.LeftButton, pos=QPoint(int(first_x), int(y)))
    assert clicked == ["2026-06-20"]
    view.close()


def test_agenda_hover_blocks_and_day_click():
    from jobtracker.ui.widgets.agenda_view import _AgendaCanvas

    _application()
    canvas = _AgendaCanvas()
    canvas.resize(700, 420)
    sessions = [
        {
            "day": "2026-06-20",
            "start_h": 9.0,
            "end_h": 10.5,
            "color": "#3B82F6",
            "subject_name": "Physics",
            "duration_seconds": 5400,
        }
    ]
    canvas.set_data(
        sessions, ["2026-06-20", "2026-06-21"], hour_start=6, hour_end=23
    )
    canvas.grab()  # force a paint pass so hit rectangles exist

    assert canvas._block_hits
    rect, _block, day_key = canvas._block_hits[0]
    assert day_key == "2026-06-20"
    hit = canvas._block_at(rect.center().x(), rect.center().y())
    assert hit is not None and hit[0]["name"] == "Physics"

    html = canvas._block_html(hit[0], day_key)
    assert "Physics" in html
    assert "09:00–10:30" in html
    assert "1.5h" in html

    assert canvas._day_at(rect.center().x(), rect.center().y()) == "2026-06-20"
    clicked: list[str] = []
    canvas.day_clicked.connect(clicked.append)
    QTest.mouseClick(
        canvas,
        Qt.LeftButton,
        pos=QPoint(int(rect.center().x()), int(rect.center().y())),
    )
    assert clicked == ["2026-06-20"]


def test_agenda_left_click_targets_the_clicked_session():
    sessions = [
        {
            "session_id": 41,
            "day": "2026-06-20",
            "start_h": 9.0,
            "end_h": 10.0,
            "color": "#3B82F6",
            "subject_name": "Physics",
            "duration_seconds": 3600,
        },
        {
            "session_id": 77,
            "day": "2026-06-20",
            "start_h": 14.0,
            "end_h": 16.0,
            "color": "#22C55E",
            "subject_name": "Maths",
            "duration_seconds": 7200,
        },
    ]
    canvas = _agenda_canvas_with(sessions, ["2026-06-20", "2026-06-21"])

    picked: list[int] = []
    days: list[str] = []
    canvas.session_clicked.connect(picked.append)
    canvas.day_clicked.connect(days.append)

    # Each block routes to its OWN session, not an arbitrary one.
    for rect, block, _day in canvas._block_hits:
        QTest.mouseClick(
            canvas,
            Qt.LeftButton,
            pos=QPoint(int(rect.center().x()), int(rect.center().y())),
        )
        assert picked[-1] == block["session_id"]
    assert sorted(picked) == [41, 77]
    assert days == []


def test_agenda_right_click_matches_left_and_empty_space_opens_the_day():
    sessions = [
        {
            "session_id": 41,
            "day": "2026-06-20",
            "start_h": 9.0,
            "end_h": 10.0,
            "color": "#3B82F6",
            "subject_name": "Physics",
            "duration_seconds": 3600,
        }
    ]
    canvas = _agenda_canvas_with(sessions, ["2026-06-20", "2026-06-21"])
    rect, _block, _day = canvas._block_hits[0]

    picked: list[int] = []
    days: list[str] = []
    canvas.session_clicked.connect(picked.append)
    canvas.day_clicked.connect(days.append)

    # Right-click on a block behaves exactly like left-click: that session.
    QTest.mouseClick(
        canvas,
        Qt.RightButton,
        pos=QPoint(int(rect.center().x()), int(rect.center().y())),
    )
    assert picked == [41] and days == []

    # Left-click on empty column space -> whole day too.
    column_rect, day_key = canvas._column_hits[1]
    QTest.mouseClick(
        canvas,
        Qt.LeftButton,
        pos=QPoint(int(column_rect.center().x()), int(column_rect.bottom()) - 6),
    )
    assert days == [day_key]
    assert picked == [41]


def test_agenda_live_block_without_id_falls_back_to_day():
    sessions = [
        {
            "session_id": None,  # the running session
            "day": "2026-06-20",
            "start_h": 9.0,
            "end_h": 10.0,
            "color": "#3B82F6",
            "subject_name": "Physics",
            "duration_seconds": 3600,
        }
    ]
    canvas = _agenda_canvas_with(sessions, ["2026-06-20"])
    rect, _block, _day = canvas._block_hits[0]

    picked: list[int] = []
    days: list[str] = []
    canvas.session_clicked.connect(picked.append)
    canvas.day_clicked.connect(days.append)
    QTest.mouseClick(
        canvas,
        Qt.LeftButton,
        pos=QPoint(int(rect.center().x()), int(rect.center().y())),
    )
    assert picked == [] and days == ["2026-06-20"]


def test_agenda_click_opens_editor_for_that_session_in_main_window(
    database, monkeypatch
):
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Physics", "#3B82F6", "")
        start = (datetime.now() - timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        session = window.service.add_session(
            subject.id, start, start + timedelta(hours=1)
        )

        window._open_session_editor(session.id)
        qt_app.processEvents()
        editor = window.findChild(SessionDialog)
        assert editor is not None and editor.session.id == session.id

        # Saving keeps the row id and applies the change (shared edit path).
        editor._shift(3600)  # nudge an hour later with the Move buttons
        editor.accept()
        QTest.qWait(30)
        qt_app.processEvents()
        updated = window.service.get_session(session.id)
        assert updated.id == session.id
        assert datetime.fromisoformat(updated.start_time) == start + timedelta(hours=1)
        assert updated.duration_seconds == 3600
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_agenda_block_click_can_delete_that_session(database, monkeypatch):
    """The agenda is the only route to this editor, so delete must work there."""
    from jobtracker.ui.widgets import session_dialog as session_dialog_module
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Physics", "#3B82F6", "")
        start = (datetime.now() - timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        doomed = window.service.add_session(
            subject.id, start, start + timedelta(hours=1)
        )
        keeper = window.service.add_session(
            subject.id, start + timedelta(hours=3), start + timedelta(hours=4)
        )

        # Click the agenda block -> shared editor -> confirmed delete.
        window._open_session_editor(doomed.id)
        QTest.qWait(30)
        qt_app.processEvents()
        editor = window.findChild(SessionDialog)
        assert editor is not None and editor.session.id == doomed.id

        monkeypatch.setattr(
            session_dialog_module,
            "question",
            lambda _p, _t, _x, on_finished, **_kw: on_finished(QMessageBox.Yes),
        )
        editor._confirm_delete()
        QTest.qWait(30)
        qt_app.processEvents()

        assert window.service.get_session(doomed.id) is None
        assert window.service.get_session(keeper.id) is not None  # nothing else touched
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_graph_settings_offers_a_bucket_size_and_persists_it(database, monkeypatch):
    """The desktop half of the same choice the phone got."""
    qt_app, window = _window(database, monkeypatch)
    try:
        from jobtracker.ui.widgets.graph_settings_dialog import (
            GROUPING_OPTIONS,
            GraphSettingsDialog,
        )

        assert [value for _, value in GROUPING_OPTIONS] == [
            "auto", "daily", "weekly", "monthly",
        ]

        dialog = GraphSettingsDialog(window, service=window.service)
        # Defaults to automatic, i.e. exactly the old behaviour.
        assert dialog.grouping_combo.currentData() == "auto"

        daily_idx = [v for _, v in GROUPING_OPTIONS].index("daily")
        dialog.grouping_combo.setCurrentIndex(daily_idx)
        from PySide6.QtWidgets import QDialog

        window._finish_graph_settings(QDialog.Accepted, dialog)

        assert window._graph_grouping == "daily"
        dialog.deleteLater()
    finally:
        window.close()


def test_bucket_size_override_changes_the_bars_drawn(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Physics", "#3B82F6", "")
        base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        for offset in range(28):
            day = base - timedelta(days=offset)
            window.service.add_session(subject.id, day, day + timedelta(hours=1))

        window._graph_range_preset = "months"
        window._graph_custom_range = None

        window._graph_grouping = "auto"
        _, _, automatic = window._window_and_grouping()
        assert automatic == "weekly"

        window._graph_grouping = "daily"
        _, _, overridden = window._window_and_grouping()
        assert overridden == "daily"

        window._reload_graphs()
        qt_app.processEvents()
        assert len(window._graph_view._canvas._data) > 7
    finally:
        window.close()


def test_a_saved_heatmap_view_mode_falls_back_to_bars(database, monkeypatch):
    """Anyone whose last view was the removed heatmap must not get a blank page."""
    database.set_setting("graph_view_mode", "heatmap")
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._graph_view_mode == "bar"
        assert database.get_setting("graph_view_mode") == "bar"
    finally:
        window.close()

