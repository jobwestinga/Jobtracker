"""Small offscreen integration checks for application wiring.

These deliberately avoid visual assertions. Their purpose is to catch missing
callbacks/imports and verify that all three main feature areas construct against
an isolated database.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGraphicsOpacityEffect,
    QLabel,
    QMessageBox,
)

from jobtracker.services.tracker_service import TrackerService
from jobtracker.ui import app as app_module
from jobtracker.ui.widgets.goal_dialog import (
    GoalDialog,
    GoalDetailDialog,
    apply_goal_edits,
)
from jobtracker.ui.widgets.graph_settings_dialog import GraphSettingsDialog
from jobtracker.ui.widgets.graph_settings_dialog import RANGE_OPTIONS
from jobtracker.ui.widgets.heatmap_view import (
    GAP,
    LEFT,
    HeatmapWidget,
    _intensity_ratio,
    _mix_color,
    _scale_colors,
    _visual_ratio,
)
from jobtracker.ui.widgets.settings_dialog import SettingsDialog
from jobtracker.ui.widgets.reorderable_list import ReorderableCardList
from jobtracker.ui.widgets.template_dialog import TemplateDialog, TemplateManagerDialog
from jobtracker.ui.widgets.todo_task_item import TodoTaskItemWidget
from jobtracker.ui.widgets.todo_task_item import _StarBurstOverlay


def _application():
    return QApplication.instance() or QApplication([])


def _window(database, monkeypatch):
    qt_app = _application()
    monkeypatch.setattr(app_module, "TrackerService", lambda: TrackerService(database))
    window = app_module.MainWindow(qt_app)
    window.show()
    qt_app.processEvents()
    return qt_app, window


def test_main_window_constructs_with_goals_and_heatmap(database, monkeypatch):
    database.set_setting("graph_grouping", "weekly")
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._pages.count() == 3
        assert [button.text() for button in window._nav_buttons] == [
            "Goals", "Subjects", "Graphs",
        ]
        assert database.get_setting("graph_grouping", "") == ""
        assert window._recurring_timer.isActive()
        assert window._pages.currentIndex() == 1
        assert window._graph_stack.count() == 3
        assert not hasattr(window, "_tasks_nav_btn")
        assert set(window._graph_mode_buttons) == {"bar", "agenda", "heatmap"}
        window._switch_page(2)
        qt_app.processEvents()
        assert (
            window._graph_mode_buttons["bar"].x()
            < window._graph_mode_buttons["agenda"].x()
            < window._graph_mode_buttons["heatmap"].x()
            < window._graph_settings_btn.x()
        )

        goal = window.service.add_todo_task("Build project", "Outcome", None)
        window.service.add_milestone(goal.id, "Prototype")
        window._reload_tasks()
        assert window._todo_list.count() == 1

        old_subject = window.service.add_subject("Old history", "#22C55E", "")
        old_start = datetime.now() - timedelta(days=60)
        window.service.add_session(
            old_subject.id, old_start, old_start + timedelta(hours=1)
        )
        window._graph_range_preset = "weeks"
        window._graph_mode_buttons["heatmap"].click()
        assert window._graph_stack.currentIndex() == 2
        assert database.get_setting("graph_view_mode") == "heatmap"
        assert old_start.date().isoformat() in window._heatmap_view._canvas._data
        assert "All Time" in window._graph_subtitle.text()

        window._graph_mode_buttons["agenda"].click()
        assert window._graph_stack.currentIndex() == 1
        assert database.get_setting("graph_view_mode") == "agenda"
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


def test_check_button_gated_by_milestone_progress(service):
    qt_app = _application()
    tokens = {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa", "TEXT_DIMMED": "#777",
              "ACCENT_GREEN": "#0f0", "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
              "ACCENT": "#38f"}
    goal = service.add_todo_task("Gated", "", None)

    # 1/2 milestones done -> completion blocked; ✓ shakes, does not emit.
    blocked = TodoTaskItemWidget(goal, tokens, progress=(1, 2))
    fired = []
    blocked.complete_requested.connect(fired.append)
    blocked.show()
    qt_app.processEvents()
    blocked.check_btn.click()
    assert fired == []
    blocked.close()

    # All milestones done -> ✓ emits completion.
    ready = TodoTaskItemWidget(goal, tokens, progress=(2, 2))
    fired_ok = []
    ready.complete_requested.connect(fired_ok.append)
    ready.show()
    qt_app.processEvents()
    ready.check_btn.click()
    assert fired_ok == [goal.id]
    ready.close()


def test_goal_removal_fade_is_retained_until_completion():
    qt_app = _application()
    cards = ReorderableCardList()
    card = QLabel("Completing")
    card.setFixedHeight(60)
    cards.add_card(1, card)
    cards.resize(400, 180)
    cards.show()
    qt_app.processEvents()

    cards.animate_remove(1)

    assert 1 in cards._remove_anims
    assert isinstance(card.graphicsEffect(), QGraphicsOpacityEffect)
    cards._remove_anims[1].stop()
    cards.close()


def test_completed_goal_card_can_only_be_reopened_from_detail(service):
    qt_app = _application()
    goal = service.add_todo_task("Restore me", "", None)
    assert service.complete_goal(goal.id)
    completed = service.get_goal(goal.id)
    card = TodoTaskItemWidget(completed, {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa",
                                          "TEXT_DIMMED": "#777", "ACCENT_GREEN": "#0f0",
                                          "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
                                          "ACCENT": "#38f"})
    card.show()
    qt_app.processEvents()
    # Completed cards hide the check button entirely; clicking does nothing.
    assert card.check_btn.isHidden()
    card.check_btn.click()

    # Reopen is only reachable from the detail dialog (now labelled "Reopen Goal").
    detail = GoalDetailDialog(service, goal.id)
    assert detail.complete_btn.text() == "Reopen Goal"
    detail.complete_btn.click()
    assert not service.get_goal(goal.id).is_completed
    detail.close()
    card.close()


def test_goal_edits_are_the_only_place_that_adds_or_deletes_milestones(service):
    qt_app = _application()
    goal = service.add_todo_task("Original", "Old motivation", None)
    first = service.add_milestone(goal.id, "Keep", "Old description")
    service.add_milestone(goal.id, "Remove")

    apply_goal_edits(
        service,
        goal.id,
        {
            "name": "Updated",
            "notes": "New motivation",
            "milestones": [
                {
                    "id": first.id,
                    "title": "Renamed",
                    "note": "Updated description",
                },
                {
                    "id": None,
                    "title": "Added",
                    "note": "New description",
                },
            ],
        },
    )
    assert service.get_goal(goal.id).name == "Updated"
    assert [m.title for m in service.get_goal_milestones(goal.id)] == [
        "Renamed", "Added",
    ]
    assert [m.note for m in service.get_goal_milestones(goal.id)] == [
        "Updated description", "New description",
    ]

    detail = GoalDetailDialog(service, goal.id)
    detail.show()
    qt_app.processEvents()
    assert detail.windowTitle() == "Updated"
    assert not hasattr(detail, "title_lbl")
    assert detail.desc_lbl.text() == "New motivation"
    button_texts = {button.text() for button in detail.findChildren(type(detail.edit_btn))}
    assert "Edit Goal" in button_texts
    assert "Add" not in button_texts
    assert "Remove" not in button_texts
    assert "✕" not in button_texts
    detail.close()


def test_add_milestone_button_inserts_an_editable_row():
    qt_app = _application()
    dialog = GoalDialog()
    dialog.show()
    qt_app.processEvents()
    assert len(dialog._milestone_rows) == 1

    dialog._add_milestone_btn.click()
    qt_app.processEvents()

    assert len(dialog._milestone_rows) == 2
    assert dialog._milestone_rows[-1]["input"].isVisible()
    assert dialog._milestone_rows[-1]["note_input"].isVisible()
    dialog.close()


def test_edit_goal_offers_confirmed_permanent_delete(service, monkeypatch):
    goal = service.add_todo_task("Delete from edit", "", None)
    dialog = GoalDialog(
        goal=goal,
        milestones=[],
    )
    assert dialog.delete_btn.text() == "Delete Goal"
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    dialog._confirm_delete()
    assert dialog.result() == GoalDialog.DELETE_RESULT
    dialog.close()


def test_checking_milestone_starts_completion_animation(service):
    qt_app = _application()
    goal = service.add_todo_task("Animated", "Description", None)
    milestone = service.add_milestone(goal.id, "First", "Visible detail")
    detail = GoalDetailDialog(service, goal.id)
    detail.show()
    qt_app.processEvents()
    check = detail.findChildren(QCheckBox)[0]
    check.click()
    qt_app.processEvents()
    assert service.db.get_milestone(milestone.id).is_done == 1
    burst = detail.findChildren(_StarBurstOverlay)[0]
    assert burst._origin.x() < detail.width() / 3
    detail.close()


def test_template_manager_makes_selection_and_action_clear(service):
    qt_app = _application()
    template = service.add_goal_template("Weekly review", "", "weekly", ["Review"])
    manager = TemplateManagerDialog(service)
    manager.show()
    qt_app.processEvents()
    assert manager._list.currentRow() == 0
    assert manager._edit_btn.isEnabled()
    assert manager._toggle_btn.text() == "Disable"
    assert template.title in manager._selection_label.text()
    manager.close()


def test_template_dialog_edits_schedule_and_milestone_descriptions(service):
    qt_app = _application()
    template = service.add_goal_template(
        "Weekly review",
        "Reset priorities",
        "weekly",
        [{"title": "Review goals", "note": "Check what still matters."}],
        recurrence_day=3,
    )
    dialog = TemplateDialog(template=template)
    dialog.show()
    qt_app.processEvents()

    assert dialog.recurrence_combo.currentData() == "weekly"
    assert dialog.schedule_combo.currentData() == 3
    assert dialog._milestone_rows[0]["input"].text() == "Review goals"
    assert (
        dialog._milestone_rows[0]["note_input"].toPlainText()
        == "Check what still matters."
    )

    dialog.recurrence_combo.setCurrentIndex(
        dialog.recurrence_combo.findData("monthly")
    )
    dialog.schedule_combo.setCurrentIndex(
        dialog.schedule_combo.findData(27)
    )
    data = dialog.get_data()
    assert data["recurrence"] == "monthly"
    assert data["recurrence_day"] == 27
    assert data["milestones"] == [
        {"title": "Review goals", "note": "Check what still matters."}
    ]
    dialog.close()


def test_heatmap_uses_full_height_and_continuous_single_hue_intensity():
    qt_app = _application()
    heatmap = HeatmapWidget()
    heatmap.resize(700, 600)
    heatmap.set_data(
        [
            {"date": "2026-06-19", "total_seconds": 0},
            {"date": "2026-06-20", "total_seconds": 30 * 60},
            {"date": "2026-06-21", "total_seconds": 8 * 3600},
        ]
    )
    heatmap.show()
    qt_app.processEvents()
    assert heatmap._scroll.maximumHeight() > 10000
    assert heatmap._canvas._metrics()[0] > 20
    cell = heatmap._canvas._metrics()[0]
    assert (heatmap.width() - LEFT) // (cell + GAP) >= 20
    assert _intensity_ratio(0, 8 * 3600) == 0
    assert _intensity_ratio(30 * 60, 8 * 3600) == 0.0625
    assert _intensity_ratio(8 * 3600, 8 * 3600) == 1
    low, high = _scale_colors(
        {"BG_PRIMARY": "#0B1120", "BG_TERTIARY": "#1A2640"}
    )
    half = _mix_color(low, high, 0.5)
    assert half != low
    assert half != high
    assert _mix_color(low, high, 0.51) != half
    assert _visual_ratio(0) == 0
    assert _visual_ratio(0.25) > 0.25
    assert _visual_ratio(1) == 1
    assert high == QColor("#4DFF88")

    cell, origin_x, origin_y = heatmap._canvas._metrics()
    QTest.mouseMove(
        heatmap._canvas,
        QPoint(origin_x + cell // 2, origin_y + 6 * (cell + GAP) + cell // 2),
    )
    qt_app.processEvents()
    assert heatmap._canvas._hover_card.isVisible()
    assert "Sunday, 21 June 2026" in heatmap._canvas._hover_card.text()
    assert "8.0 tracked hours" in heatmap._canvas._hover_card.text()
    heatmap.close()


def test_heatmap_mode_hides_date_range_settings():
    qt_app = _application()
    dialog = GraphSettingsDialog()
    dialog._select_mode(2)
    dialog.show()
    qt_app.processEvents()
    assert dialog._range_label.isHidden()
    assert dialog.custom_check.isHidden()
    assert dialog._heatmap_range_hint.isVisible()
    assert all(button.isHidden() for button in dialog.range_btns)
    dialog.close()


def test_graph_ranges_are_calendar_presets_with_custom_still_available():
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


def test_dark_and_light_palette_swatches_are_black_and_white():
    qt_app = _application()
    dialog = SettingsDialog()
    dialog.show()
    qt_app.processEvents()
    styles = {
        button.property("palette_name"): button.styleSheet()
        for button in dialog._palette_btns
    }
    assert "background-color: #090B0F" in styles["Dark"]
    assert "background-color: #FFFFFF" in styles["Light"]
    dialog.close()
