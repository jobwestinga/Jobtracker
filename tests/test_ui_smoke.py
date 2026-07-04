"""Small offscreen integration checks for application wiring.

These deliberately avoid visual assertions. Their purpose is to catch missing
callbacks/imports and verify that all three main feature areas construct against
an isolated database.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
)

from jobtracker.services.tracker_service import TrackerService
from jobtracker.ui import app as app_module
from jobtracker.ui.widgets import goal_dialog as goal_dialog_module
from jobtracker.ui.widgets.dialog_utils import information, open_dialog
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
from jobtracker.ui.widgets.subject_dialog import SubjectDialog
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
        qt_app.processEvents()
        assert window._graph_stack.currentIndex() == 2
        assert database.get_setting("graph_view_mode") == "heatmap"
        assert old_start.date().isoformat() in window._heatmap_view._canvas._data
        assert "All Time" in window._graph_subtitle.text()

        window._graph_mode_buttons["agenda"].click()
        qt_app.processEvents()
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


def test_latest_up_down_direction_ignores_stale_opposite_auto_repeat(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    menu = QListWidget()
    menu.addItems([f"Item {index}" for index in range(10)])
    menu.setCurrentRow(4)
    menu.show()
    menu.activateWindow()
    menu.setFocus()
    qt_app.processEvents()
    try:
        # Hold Down, then reverse to Up before the old repeat queue is empty.
        QTest.keyPress(menu, Qt.Key_Down)
        assert menu.currentRow() == 5
        QTest.keyPress(menu, Qt.Key_Up)
        assert menu.currentRow() == 4

        stale_down = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Down,
            Qt.NoModifier,
            "",
            True,
            1,
        )
        QApplication.sendEvent(menu, stale_down)
        assert menu.currentRow() == 4

        # Repeat belonging to the newly selected direction still works.
        current_up = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Up,
            Qt.NoModifier,
            "",
            True,
            1,
        )
        QApplication.sendEvent(menu, current_up)
        assert menu.currentRow() == 3
    finally:
        QTest.keyRelease(menu, Qt.Key_Up)
        QTest.keyRelease(menu, Qt.Key_Down)
        menu.close()
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_arrow_reversal_scrolls_viewport_on_first_opposite_step(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    menu = QListWidget()
    menu.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    menu.setSpacing(2)
    for index in range(12):
        menu.addItem(f"Item {index}")
        menu.item(index).setSizeHint(QSize(220, 34))
    menu.resize(260, 125)
    menu.setCurrentRow(0)
    menu.show()
    menu.activateWindow()
    menu.setFocus()
    qt_app.processEvents()
    try:
        for _ in range(6):
            QTest.keyClick(menu, Qt.Key_Down)
            qt_app.processEvents()
        before_reverse = menu.verticalScrollBar().value()
        assert before_reverse > 0

        QTest.keyClick(menu, Qt.Key_Up)
        qt_app.processEvents()

        assert menu.currentRow() == 5
        assert menu.verticalScrollBar().value() < before_reverse
    finally:
        menu.close()
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_number_shortcuts_are_context_sensitive_and_switching_needs_confirm(
    database, monkeypatch
):
    import jobtracker.ui.subjects_mixin as subjects_mixin_module

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

        # A different number offers a switch; declining changes nothing.
        prompts: list[str] = []
        monkeypatch.setattr(
            subjects_mixin_module,
            "question",
            lambda _parent, _title, text, on_finished, **_kw: (
                prompts.append(text),
                on_finished(QMessageBox.No),
            ),
        )
        QTest.keyClick(window, Qt.Key_2)
        qt_app.processEvents()
        assert prompts and "Second" in prompts[0]
        assert window.service.active_subject.id == first_subject.id
        assert window.service.active_session.id == active_session_id
        assert len(window.service.db.get_open_sessions()) == 1

        # Confirming stops the old session and starts the new one.
        monkeypatch.setattr(
            subjects_mixin_module,
            "question",
            lambda _parent, _title, _text, on_finished, **_kw: on_finished(
                QMessageBox.Yes
            ),
        )
        QTest.keyClick(window, Qt.Key_2)
        qt_app.processEvents()
        assert window.service.active_subject.id == second_subject.id
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
        app_module,
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
        qt_app.processEvents()
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


def test_stale_open_session_does_not_block_quit(database, monkeypatch):
    # Simulate a session left open by a crash / force-kill: it exists but its
    # heartbeat is hours old, so nothing is actually tracking right now.
    seed = TrackerService(database)
    subject = seed.add_subject("Left open", "#22C55E", "")
    assert seed.start_subject(subject.id)
    # A crash-leftover session is old on both ends: started hours ago and its
    # heartbeat stopped hours ago. (Backdating only last_active would be clamped
    # back up to start, so the gap must come from an old start too.)
    old_start = (datetime.now() - timedelta(hours=7)).isoformat()
    old_active = (datetime.now() - timedelta(hours=6)).isoformat()
    database.connection.execute(
        "UPDATE sessions SET start_time = ?, last_active_at = ? "
        "WHERE end_time IS NULL",
        (old_start, old_active),
    )
    database.connection.commit()

    # The recovered stale session would otherwise pop a modal recovery dialog.
    monkeypatch.setattr(
        app_module.MainWindow, "_maybe_prompt_recovery", lambda self: None
    )
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window.service.active_session is not None
        assert window.service.get_active_recovery_info() is not None
        # Idle-but-stale: quitting is allowed and the open session is preserved
        # for recovery on the next launch (never silently dropped).
        assert window.close()
        assert window.service.db.get_open_sessions()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_genuinely_active_session_still_blocks_quit(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    messages = []
    monkeypatch.setattr(
        app_module,
        "information",
        lambda *args: messages.append(args[1]),
    )
    try:
        subject = window.service.add_subject("Fresh", "#22C55E", "")
        assert window.service.start_subject(subject.id)
        # A just-started session has a current heartbeat -> genuinely tracking.
        assert window.service.get_active_recovery_info() is None
        assert not window.close()
        assert window.isVisible()
        assert messages and messages[-1] == "Session Still Active"
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        if window.service.active_session:
            started = datetime.fromisoformat(
                window.service.active_session.start_time
            )
            window.service.stop_active_subject(started + timedelta(seconds=31))
        window.close()
        qt_app.processEvents()


def test_list_refresh_reuses_untouched_cards_and_rebuilds_only_changed(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        a = window.service.add_subject("A", "#111111", "")
        b = window.service.add_subject("B", "#222222", "")
        window._reload_subjects()
        card_a = window._subjects_list.card_widget(a.id)
        card_b = window._subjects_list.card_widget(b.id)
        assert card_a is not None and card_b is not None

        # Adding a session to B changes only its transient tracked total: no card
        # is torn down (this teardown is what glitches a fullscreen window).
        start = datetime.now() - timedelta(hours=1)
        window.service.add_session(b.id, start, start + timedelta(minutes=30))
        window._reload_subjects()
        assert window._subjects_list.card_widget(a.id) is card_a
        assert window._subjects_list.card_widget(b.id) is card_b

        # Renaming B is structural -> only B's card is rebuilt; A is untouched.
        window.service.update_subject(b.id, "B renamed", b.color, "")
        window._reload_subjects()
        assert window._subjects_list.card_widget(a.id) is card_a
        b_card_after_rename = window._subjects_list.card_widget(b.id)
        assert b_card_after_rename is not card_b

        # Archiving A drops it from the active list without rebuilding B.
        window._archive_subject(a.id)
        assert window._subjects_list.card_widget(a.id) is None
        assert window._subjects_list.card_widget(b.id) is b_card_after_rename
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_tracking_transitions_defer_and_update_cards_in_place(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Deferred", "#22C55E", "")
        window._reload_subjects()

        # A start/stop must not tear down and rebuild the card list — that heavy
        # rebuild is what glitches a fullscreen macOS window into a Space switch.
        graph_calls = []
        window._reload_graphs = lambda: graph_calls.append("graphs")
        rebuilds = []
        original_reload_subjects = window._reload_subjects
        window._reload_subjects = lambda: (
            rebuilds.append("subjects"),
            original_reload_subjects(),
        )

        card = window._subjects_list.card_widget(subject.id)
        assert card is not None and not card.is_active

        window._start_tracking(subject.id)
        assert window.service.active_session is not None
        # Deferred: nothing mutates until the click event returns to Qt.
        assert window._tracking_refresh_timer.isActive()
        assert not card.is_active

        qt_app.processEvents()
        # State reflected in place: same card object, no full rebuild, and the
        # hidden Graphs page is not redrawn from the Subjects page.
        assert card.is_active
        assert window._timer.subject is not None
        assert rebuilds == []
        assert graph_calls == []

        window._stop_tracking()
        assert window.service.active_session is None
        assert window._tracking_refresh_timer.isActive()

        qt_app.processEvents()
        assert not card.is_active
        assert window._timer.subject is None
        assert rebuilds == []

        window._reload_subjects = original_reload_subjects
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
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
        open_dialog(detail)
        qt_app.processEvents()
        key_target = detail._milestone_checks[0]
        key_target.setFocus()
        qt_app.processEvents()
        QTest.keyClick(key_target, Qt.Key_1)
        assert window.service.db.get_milestone(milestone.id).is_done == 1
        QTest.keyClick(key_target, Qt.Key_Z, Qt.ControlModifier)
        assert window.service.db.get_milestone(milestone.id).is_done == 0
        detail.close()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_reorderable_list_restores_scroll_without_task_selection():
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
    assert (
        card_list.selectionMode()
        == QAbstractItemView.NoSelection
    )
    card_list.verticalScrollBar().setValue(
        card_list.verticalScrollBar().maximum() // 2
    )
    qt_app.processEvents()
    state = card_list.capture_view_state()

    card_list.clear_cards()
    populate()
    card_list.restore_view_state(state)
    qt_app.processEvents()

    assert card_list.verticalScrollBar().value() == state["scroll"]
    card_list.close()


def test_reorderable_list_arrows_scroll_one_card_from_first_press():
    qt_app = _application()
    card_list = ReorderableCardList(spacing=6)
    card_list.resize(360, 180)
    for item_id in range(1, 9):
        card = QFrame()
        card.setFixedHeight(50)
        card_list.add_card(item_id, card)
        card_list.item(card_list.count() - 1).setSizeHint(QSize(300, 50))
    card_list.show()
    card_list.activateWindow()
    card_list.setFocus()
    qt_app.processEvents()

    assert card_list.verticalScrollBar().value() == 0
    QTest.keyClick(card_list, Qt.Key_Down)
    qt_app.processEvents()
    assert card_list.verticalScrollBar().value() == 56
    assert card_list.selectedItems() == []

    QTest.keyClick(card_list, Qt.Key_Up)
    qt_app.processEvents()
    assert card_list.verticalScrollBar().value() == 0
    assert card_list.selectedItems() == []
    card_list.close()


def test_goal_detail_number_shortcuts_toggle_matching_milestones(service):
    qt_app = _application()
    host = QFrame()
    host.resize(700, 800)
    host.show()
    goal = service.add_todo_task("Keyboard goal", "", None)
    first = service.add_milestone(goal.id, "First")
    second = service.add_milestone(goal.id, "Second")
    detail = GoalDetailDialog(service, goal.id, host)
    open_dialog(detail)
    qt_app.processEvents()
    key_target = detail._milestone_checks[1]
    key_target.setFocus()

    QTest.keyClick(key_target, Qt.Key_2)
    assert service.db.get_milestone(first.id).is_done == 0
    assert service.db.get_milestone(second.id).is_done == 1
    QTest.qWait(350)
    qt_app.processEvents()

    key_target = detail._milestone_checks[1]
    key_target.setFocus()
    qt_app.processEvents()
    QTest.keyClick(key_target, Qt.Key_2)
    assert service.db.get_milestone(second.id).is_done == 0
    detail.reject()
    qt_app.processEvents()

    service.set_milestone_done(first.id, True)
    service.set_milestone_done(second.id, True)
    assert service.complete_goal(goal.id)
    completed_detail = GoalDetailDialog(service, goal.id, host)
    open_dialog(completed_detail)
    qt_app.processEvents()
    completed_target = completed_detail._milestone_checks[0]
    completed_target.setFocus()
    QTest.keyClick(completed_target, Qt.Key_1)
    assert service.db.get_milestone(first.id).is_done == 1
    assert service.get_goal(goal.id).is_completed == 1
    completed_detail.reject()
    qt_app.processEvents()
    host.close()


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
    qt_app.processEvents()

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


def test_parented_editors_are_widgets_from_birth_and_accept_text():
    qt_app = _application()
    parent = QFrame()
    parent.show()
    dialogs = (GoalDialog(parent), SubjectDialog(parent))
    try:
        for dialog in dialogs:
            assert dialog.windowType() == Qt.Widget
            assert dialog.windowModality() == Qt.NonModal
            assert not dialog.isWindow()
            dialog.show()
            editor = (
                dialog.name_input
                if hasattr(dialog, "name_input")
                else dialog.title_input
            )
            editor.setFocus()
            qt_app.processEvents()
            QTest.keyClicks(editor, "Typed")
            assert editor.text().endswith("Typed")
            dialog.hide()
    finally:
        for dialog in dialogs:
            dialog.close()
        parent.close()


def test_new_subject_and_goal_use_deferred_inline_panels(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._pages.currentIndex() == 1
        window._nav_buttons[0].click()
        assert window._pages.currentIndex() == 1
        qt_app.processEvents()
        assert window._pages.currentIndex() == 0
        window._switch_page(1)

        window._new_subject()
        subject_dialog = window.findChild(SubjectDialog)
        assert subject_dialog is not None
        assert not subject_dialog.isVisible()
        qt_app.processEvents()
        assert subject_dialog.isVisible()
        assert subject_dialog.windowType() == Qt.Widget
        assert subject_dialog.window() is window
        assert not window._shortcut_focus_allows_navigation()
        subject_dialog.name_input.setText("Deferred subject")
        subject_dialog.accept()
        assert not window.service.get_all_subjects()
        qt_app.processEvents()
        assert [s.name for s in window.service.get_all_subjects()] == [
            "Deferred subject"
        ]
        qt_app.processEvents()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 0

        window._new_goal()
        goal_dialog = window.findChild(GoalDialog)
        assert goal_dialog is not None
        assert not goal_dialog.isVisible()
        qt_app.processEvents()
        assert goal_dialog.isVisible()
        assert goal_dialog.windowType() == Qt.Widget
        assert goal_dialog.window() is window
        goal_dialog.title_input.setText("Deferred goal")
        goal_dialog.accept()
        assert not window.service.get_active_goals()
        qt_app.processEvents()
        assert [g.name for g in window.service.get_active_goals()] == [
            "Deferred goal"
        ]
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_message_box_helper_uses_parented_window_modal_dialog():
    qt_app = _application()
    parent = QFrame()
    box = information(parent, "Title", "Body")
    assert box._jt_original_parent is parent
    qt_app.processEvents()
    assert box.windowType() == Qt.Widget
    assert box.window() is parent
    assert not box.isWindow()
    box.done(QMessageBox.Ok)
    qt_app.processEvents()
    parent.close()


def test_framework_color_and_file_panels_remain_window_modal(service):
    qt_app = _application()
    parent = QFrame()
    parent.show()

    subject = SubjectDialog(parent)
    subject.show()
    subject._pick_custom()
    color = subject.findChild(QColorDialog)
    assert color is not None and not color.isVisible()
    qt_app.processEvents()
    assert color.isVisible()
    assert color.testOption(QColorDialog.DontUseNativeDialog)
    assert color.windowType() == Qt.Dialog
    assert color.windowModality() == Qt.WindowModal
    assert color.isWindow()
    color.reject()
    qt_app.processEvents()
    subject.close()

    settings = SettingsDialog(parent, service=service)
    settings.show()
    settings._export()
    file_dialog = settings.findChild(QFileDialog)
    assert file_dialog is not None and not file_dialog.isVisible()
    qt_app.processEvents()
    assert file_dialog.isVisible()
    assert file_dialog.testOption(QFileDialog.DontUseNativeDialog)
    assert file_dialog.windowType() == Qt.Dialog
    assert file_dialog.windowModality() == Qt.WindowModal
    assert file_dialog.isWindow()
    file_dialog.reject()
    qt_app.processEvents()
    settings.close()
    parent.close()


def test_nested_goal_editor_stays_in_main_window(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        goal = window.service.add_todo_task("Nested editor", "", None)
        window._open_goal(goal.id)
        qt_app.processEvents()
        detail = window.findChild(GoalDetailDialog)
        assert detail is not None and detail.isVisible()
        assert detail.window() is window
        assert int(window.property("_jt_inline_dialog_count") or 0) == 1

        detail._edit_goal()
        qt_app.processEvents()
        editor = window.findChild(GoalDialog)
        assert editor is not None and editor.isVisible()
        assert editor.window() is window
        assert int(window.property("_jt_inline_dialog_count") or 0) == 2

        editor.reject()
        qt_app.processEvents()
        assert detail.isVisible()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 1

        detail.accept()
        qt_app.processEvents()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 0
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_edit_goal_offers_confirmed_permanent_delete(service, monkeypatch):
    goal = service.add_todo_task("Delete from edit", "", None)
    dialog = GoalDialog(
        goal=goal,
        milestones=[],
    )
    assert dialog.delete_btn.text() == "Delete Goal"
    monkeypatch.setattr(
        goal_dialog_module,
        "question",
        lambda _parent, _title, _text, on_finished, **_kwargs: on_finished(
            QMessageBox.Yes
        ),
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


# ── switch-with-confirm, graph hover/click, quit auto-backup ─────────────────
def test_clicking_other_subject_asks_then_switches(database, monkeypatch):
    import jobtracker.ui.subjects_mixin as subjects_mixin_module

    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#3B82F6", "")
        second = window.service.add_subject("Second", "#22C55E", "")
        window._reload_subjects()
        window._start_tracking(first.id)
        qt_app.processEvents()
        assert window.service.active_subject.id == first.id

        # Decline: still tracking the first subject.
        monkeypatch.setattr(
            subjects_mixin_module,
            "question",
            lambda _parent, _title, _text, on_finished, **_kw: on_finished(
                QMessageBox.No
            ),
        )
        window._start_tracking(second.id)
        qt_app.processEvents()
        assert window.service.active_subject.id == first.id

        # Confirm: switched, exactly one open session.
        monkeypatch.setattr(
            subjects_mixin_module,
            "question",
            lambda _parent, _title, _text, on_finished, **_kw: on_finished(
                QMessageBox.Yes
            ),
        )
        window._start_tracking(second.id)
        qt_app.processEvents()
        assert window.service.active_subject.id == second.id
        assert len(window.service.db.get_open_sessions()) == 1

        # Clicking the already-active subject never prompts.
        def _fail(*_args, **_kwargs):
            raise AssertionError("question() must not be called for same subject")

        monkeypatch.setattr(subjects_mixin_module, "question", _fail)
        window._start_tracking(second.id)
        qt_app.processEvents()
        assert window.service.active_subject.id == second.id

        window.service.stop_active_subject()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


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


def test_quitting_writes_rotating_auto_backup(database, monkeypatch, tmp_path):
    import json

    import jobtracker.ui.app as app_module_local

    backups_dir = tmp_path / "backups"
    monkeypatch.setattr(app_module_local, "BACKUPS_DIR", backups_dir)
    qt_app, window = _window(database, monkeypatch)
    try:
        window.service.add_subject("Backup me", "#3B82F6", "")
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()

    files = list(backups_dir.glob("autobackup_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert any(s["name"] == "Backup me" for s in payload["subjects"])
