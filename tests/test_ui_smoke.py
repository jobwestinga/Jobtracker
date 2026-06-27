"""Small offscreen integration checks for application wiring.

These deliberately avoid visual assertions. Their purpose is to catch missing
callbacks/imports and verify that all three main feature areas construct against
an isolated database.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from jobtracker.services.tracker_service import TrackerService
from jobtracker.ui import app as app_module
from jobtracker.ui.widgets.todo_task_item import TodoTaskItemWidget


def _application():
    return QApplication.instance() or QApplication([])


def _window(database, monkeypatch):
    qt_app = _application()
    monkeypatch.setattr(app_module, "db", database)
    monkeypatch.setattr(app_module, "TrackerService", lambda: TrackerService(database))
    window = app_module.MainWindow(qt_app)
    window.show()
    qt_app.processEvents()
    return qt_app, window


def test_main_window_constructs_with_goals_and_heatmap(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._pages.count() == 3
        assert [button.text() for button in window._nav_buttons] == [
            "Subjects", "Goals", "Graphs",
        ]
        assert window._graph_stack.count() == 3

        goal = window.service.add_todo_task("Build project", "Outcome", None)
        window.service.add_milestone(goal.id, "Prototype")
        window._reload_tasks()
        assert window._todo_list.count() == 1

        window._graph_view_mode = "heatmap"
        window._reload_graphs()
        assert window._graph_stack.currentIndex() == 2
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_startup_generates_due_template_once(database, monkeypatch):
    seed = TrackerService(database)
    seed.add_goal_template("Daily routine", "", "daily", ["Cube", "Chess"])

    qt_app, window = _window(database, monkeypatch)
    try:
        goals = window.service.get_active_goals()
        assert len(goals) == 1
        assert window.service.get_goal_progress(goals[0].id) == (0, 2)
        window._generate_due_goals()
        assert len(window.service.get_active_goals()) == 1
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_clicking_goal_card_emits_open_request(service):
    qt_app = _application()
    goal = service.add_todo_task("Open me", "", None)
    card = TodoTaskItemWidget(goal, {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa",
                                     "TEXT_DIMMED": "#777", "ACCENT_GREEN": "#0f0",
                                     "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
                                     "ACCENT": "#38f"})
    opened = []
    card.open_requested.connect(opened.append)
    card.resize(500, 90)
    card.show()
    qt_app.processEvents()
    QTest.mouseClick(card, Qt.LeftButton, pos=card.rect().center())
    assert opened == [goal.id]
    card.close()


def test_completed_goal_card_reopens_in_one_click(service):
    qt_app = _application()
    goal = service.add_todo_task("Reopen me", "", None)
    assert service.complete_goal(goal.id)
    completed = service.get_goal(goal.id)
    card = TodoTaskItemWidget(completed, {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa",
                                          "TEXT_DIMMED": "#777", "ACCENT_GREEN": "#0f0",
                                          "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
                                          "ACCENT": "#38f"})
    reopened = []
    card.reopen_requested.connect(reopened.append)
    card.show()
    qt_app.processEvents()
    card.check_btn.click()
    assert reopened == [goal.id]
    card.close()
