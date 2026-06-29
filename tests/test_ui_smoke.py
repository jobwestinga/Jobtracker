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
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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


def test_left_right_arrows_switch_main_pages_but_not_text_cursor(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        assert window._pages.currentIndex() == 1

        QTest.keyClick(window, Qt.Key_Left)
        assert window._pages.currentIndex() == 0
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)
        assert window._pages.currentIndex() == 2

        editor = QLineEdit(window)
        editor.setText("abc")
        editor.show()
        editor.setFocus()
        qt_app.processEvents()
        QTest.keyClick(editor, Qt.Key_Left)
        assert window._pages.currentIndex() == 2
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_number_shortcuts_are_context_sensitive_and_never_switch_sessions(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        first_subject = window.service.add_subject("First", "#111111", "")
        second_subject = window.service.add_subject("Second", "#222222", "")
        window._reload_subjects()
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()

        QTest.keyClick(window, Qt.Key_1)
        assert window.service.active_subject.id == first_subject.id
        active_session_id = window.service.active_session.id

        # A different number cannot stop or switch the running session.
        QTest.keyClick(window, Qt.Key_2)
        assert window.service.active_subject.id == first_subject.id
        assert window.service.active_session.id == active_session_id
        assert len(window.service.db.get_open_sessions()) == 1

        started = datetime.fromisoformat(window.service.active_session.start_time)
        window.service.stop_active_subject(started + timedelta(seconds=31))
        window._reload_subjects()

        # Archived subjects are never startable through number shortcuts.
        window.service.archive_subject(second_subject.id)
        window._showing_archived = True
        window._reload_subjects()
        QTest.keyClick(window, Qt.Key_1)
        assert window.service.active_session is None

        window._showing_archived = False
        first_goal = window.service.add_todo_task("First goal", "", None)
        second_goal = window.service.add_todo_task("Second goal", "", None)
        window._switch_page(0)
        window._reload_tasks()
        opened = []
        window._open_goal = opened.append
        QTest.keyClick(window, Qt.Key_2)
        QTest.qWait(160)
        assert opened == [second_goal.id]
        assert first_goal.id != second_goal.id

        window._switch_page(2)
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        for key, mode in (
            (Qt.Key_1, "bar"),
            (Qt.Key_2, "agenda"),
            (Qt.Key_3, "heatmap"),
        ):
            QTest.keyClick(window, key)
            assert window._graph_view_mode == mode
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_active_session_blocks_quit_and_indicator_only_returns_to_subjects(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append((args[1], args[2])),
    )
    try:
        subject = window.service.add_subject("Protected", "#22C55E", "")
        assert window.service.start_subject(subject.id)
        session_id = window.service.active_session.id
        window._reload_subjects()

        window._switch_page(0)
        qt_app.processEvents()
        assert window._active_session_indicator.isVisible()
        assert "Protected" in window._active_session_indicator.text()

        window._active_session_indicator.click()
        assert window._pages.currentIndex() == 1
        assert window.service.active_session.id == session_id

        window._switch_page(2)
        assert not window.close()
        qt_app.processEvents()
        assert window.isVisible()
        assert window._pages.currentIndex() == 1
        assert window.service.active_session.id == session_id
        assert window.service.db.get_session(session_id).end_time is None
        assert messages and messages[-1][0] == "Session Still Active"

        started = datetime.fromisoformat(window.service.active_session.start_time)
        window.service.stop_active_subject(started + timedelta(seconds=31))
        window._reload_subjects()
        assert window.close()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        if window.service.active_session:
            window.service.stop_active_subject(
                datetime.fromisoformat(window.service.active_session.start_time)
                + timedelta(seconds=31)
            )
        window.close()
        qt_app.processEvents()


def test_escape_graph_ranges_and_shortcut_badges(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("First subject", "#123456", "")
        goal = window.service.add_todo_task("First goal", "", None)
        window._reload_subjects()
        window._reload_tasks()

        subject_card = window._subjects_list.itemWidget(
            window._subjects_list.item(0)
        )
        assert any(
            label.text() == "1"
            for label in subject_card.findChildren(QLabel)
        )
        goal_card = window._todo_list.itemWidget(window._todo_list.item(0))
        assert any(
            label.text() == "1"
            for label in goal_card.findChildren(QLabel)
        )
        assert window._graph_mode_buttons["bar"].text().startswith("1")
        assert subject.id is not None and goal.id is not None

        window._switch_page(1)
        window._toggle_archived_view()
        assert window._showing_archived
        QTest.keyClick(window, Qt.Key_Escape)
        assert not window._showing_archived

        window._switch_page(0)
        window._toggle_completed_goals()
        assert window._showing_completed_goals
        QTest.keyClick(window, Qt.Key_Escape)
        assert not window._showing_completed_goals

        window._switch_page(2)
        for key, preset in (
            (Qt.Key_W, "weeks"),
            (Qt.Key_M, "months"),
            (Qt.Key_Y, "year"),
            (Qt.Key_A, "all"),
        ):
            QTest.keyClick(window, key)
            assert window._graph_range_preset == preset
            assert database.get_setting("graph_range") == preset
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_one_level_undo_restores_subject_archive_order_and_milestone(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._archive_subject(first.id)
        assert [s.id for s in window.service.get_all_subjects()] == [second.id]
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
        assert [s.id for s in window.service.get_all_subjects()] == [
            first.id,
            second.id,
        ]

        window._on_subject_order_changed([second.id, first.id])
        assert [s.id for s in window.service.get_all_subjects()] == [
            second.id,
            first.id,
        ]
        window._perform_undo(force=True)
        assert [s.id for s in window.service.get_all_subjects()] == [
            first.id,
            second.id,
        ]

        goal = window.service.add_todo_task("Undo goal", "", None)
        milestone = window.service.add_milestone(goal.id, "Undo milestone")
        detail = GoalDetailDialog(window.service, goal.id, window)
        detail.show()
        detail.activateWindow()
        detail.setFocus()
        qt_app.processEvents()
        QTest.keyClick(detail, Qt.Key_1)
        assert window.service.db.get_milestone(milestone.id).is_done == 1
        QTest.keyClick(detail, Qt.Key_Z, Qt.ControlModifier)
        assert window.service.db.get_milestone(milestone.id).is_done == 0
        detail.close()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_reorderable_list_restores_scroll_and_selection_after_rebuild():
    qt_app = _application()
    card_list = ReorderableCardList(spacing=6)
    card_list.resize(360, 180)

    def populate():
        for item_id in range(1, 13):
            card = QFrame()
            card.setFixedHeight(50)
            card_list.add_card(item_id, card)

    populate()
    card_list.show()
    qt_app.processEvents()
    card_list.setCurrentRow(8)
    card_list.verticalScrollBar().setValue(
        card_list.verticalScrollBar().maximum() // 2
    )
    qt_app.processEvents()
    state = card_list.capture_view_state()

    card_list.clear_cards()
    populate()
    card_list.restore_view_state(state)
    qt_app.processEvents()

    assert card_list.currentItem().data(Qt.UserRole) == 9
    assert card_list.verticalScrollBar().value() == state["scroll"]
    card_list.close()


def test_goal_detail_number_shortcuts_toggle_matching_milestones(service):
    qt_app = _application()
    goal = service.add_todo_task("Keyboard goal", "", None)
    first = service.add_milestone(goal.id, "First")
    second = service.add_milestone(goal.id, "Second")
    detail = GoalDetailDialog(service, goal.id)
    detail.show()
    detail.activateWindow()
    detail.setFocus()
    qt_app.processEvents()

    QTest.keyClick(detail, Qt.Key_2)
    assert service.db.get_milestone(first.id).is_done == 0
    assert service.db.get_milestone(second.id).is_done == 1
    QTest.qWait(350)
    qt_app.processEvents()

    detail.activateWindow()
    detail.setFocus()
    qt_app.processEvents()
    QTest.keyClick(detail, Qt.Key_2)
    assert service.db.get_milestone(second.id).is_done == 0
    detail.close()

    service.set_milestone_done(first.id, True)
    service.set_milestone_done(second.id, True)
    assert service.complete_goal(goal.id)
    completed_detail = GoalDetailDialog(service, goal.id)
    completed_detail.show()
    completed_detail.activateWindow()
    qt_app.processEvents()
    QTest.keyClick(completed_detail, Qt.Key_1)
    assert service.db.get_milestone(first.id).is_done == 1
    assert service.get_goal(goal.id).is_completed == 1
    completed_detail.close()


def test_reorderable_list_uses_floating_drag_and_live_reflow():
    qt_app = _application()
    card_list = ReorderableCardList(spacing=6)
    card_list.resize(360, 260)
    for item_id in (1, 2, 3):
        card = QFrame()
        card.setFixedHeight(58)
        card_list.add_card(item_id, card)
    card_list.show()
    qt_app.processEvents()

    emitted = []
    card_list.order_changed.connect(emitted.append)
    widget = card_list.itemWidget(card_list.item(2))
    center = widget.rect().center()
    QTest.mousePress(widget, Qt.LeftButton, pos=center)
    QTest.mouseMove(
        widget,
        QPoint(center.x(), center.y() - 140),
        delay=30,
    )
    qt_app.processEvents()
    assert card_list._drag_overlay is not None
    assert card_list._wobble_timer.isActive()
    assert card_list._auto_scroll_timer.isActive()
    assert card_list.ordered_ids() == [3, 1, 2]
    QTest.qWait(120)
    qt_app.processEvents()
    assert card_list._drag_overlay.width() < widget.width()

    QTest.mouseRelease(
        card_list.viewport(),
        Qt.LeftButton,
        pos=QPoint(card_list.viewport().width() // 2, 5),
    )
    QTest.qWait(180)
    qt_app.processEvents()
    assert emitted == [[3, 1, 2]]
    assert card_list._drag_overlay is None
    card_list.close()


def test_live_reorder_immediately_renumbers_badges_across_top_nine_boundary():
    qt_app = _application()
    card_list = ReorderableCardList(spacing=4)
    card_list.resize(360, 620)
    badges = {}
    for item_id in range(1, 11):
        card = QFrame()
        card.setFixedHeight(50)
        layout = QHBoxLayout(card)
        badge = QLabel(str(item_id))
        badge.setProperty("_jt_shortcut_badge", True)
        badge.setProperty(
            "_jt_shortcut_tooltip", "Press {number} to open"
        )
        badge.setVisible(item_id <= 9)
        layout.addWidget(badge)
        layout.addStretch()
        badges[item_id] = badge
        card_list.add_card(item_id, card)

    card_list.show()
    qt_app.processEvents()
    dragged = card_list.itemWidget(card_list.item(9))
    card_list._begin_drag(
        10, dragged.mapToGlobal(dragged.rect().center())
    )
    old_pixmap_key = card_list._drag_pixmap.cacheKey()

    card_list._animate_live_move(9, 0)

    assert card_list.ordered_ids()[0] == 10
    assert badges[10].text() == "1"
    assert not badges[10].isHidden()
    assert badges[10].toolTip() == "Press 1 to open"
    assert badges[9].text() == "10"
    assert badges[9].isHidden()
    assert card_list._drag_pixmap.cacheKey() != old_pixmap_key

    card_list._cancel_drag()
    card_list.close()


def test_reorderable_list_auto_scrolls_while_dragging_at_an_edge():
    qt_app = _application()
    card_list = ReorderableCardList(spacing=6)
    card_list.resize(360, 180)
    for item_id in range(1, 13):
        card = QFrame()
        card.setFixedHeight(50)
        card_list.add_card(item_id, card)
    card_list.show()
    qt_app.processEvents()

    scroll_bar = card_list.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.maximum())
    qt_app.processEvents()
    widget = card_list.itemWidget(card_list.item(11))
    card_list._begin_drag(12, widget.mapToGlobal(widget.rect().center()))
    card_list._drag_global_pos = card_list.viewport().mapToGlobal(QPoint(20, 0))
    before = scroll_bar.value()
    card_list._tick_auto_scroll()
    assert scroll_bar.value() < before
    card_list._cancel_drag()
    card_list.close()


def test_completed_goals_and_archived_subjects_remain_reorderable(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        goal = window.service.add_todo_task("Done", "", None)
        window.service.complete_goal(goal.id)
        window._showing_completed_goals = True
        window._reload_tasks()
        assert window._todo_list.dragEnabled()

        subject = window.service.add_subject("Archived", "#123456", "")
        window.service.archive_subject(subject.id)
        window._showing_archived = True
        window._reload_subjects()
        assert window._subjects_list.dragEnabled()
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
    heatmap.activateWindow()
    qt_app.processEvents()
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
