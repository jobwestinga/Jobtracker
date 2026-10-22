"""Offscreen native keyboard regressions on isolated databases."""
import time
from PySide6.QtCore import (QEvent, QSize, Qt)
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton)
from jobtracker.ui.widgets.dialog_utils import (open_dialog)
from jobtracker.ui.widgets.goal_dialog import (GoalDetailDialog, GoalDialog)
from jobtracker.ui.widgets.reorderable_list import (ReorderableCardList)
from tests.support.native import (_application, _window)

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

        # A previously focused sidebar tab must follow arrow navigation rather
        # than leaving a second accent ring on the old tab.
        window._nav_buttons[3].setFocus()
        qt_app.processEvents()
        QTest.keyClick(window._nav_buttons[3], Qt.Key_Left)
        assert window._pages.currentIndex() == 1
        assert [button.hasFocus() for button in window._nav_buttons] == [False, True, False, False]
        assert [button.isChecked() for button in window._nav_buttons] == [False, True, False, False]

        editor = QLineEdit(window)
        editor.setText("abc")
        editor.show()
        editor.setFocus()
        qt_app.processEvents()
        QTest.keyClick(editor, Qt.Key_Left)
        assert window._pages.currentIndex() == 1
    finally:
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


def test_number_shortcuts_only_open_the_exact_goal_and_move_focus(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("First subject", "#111111", "")
        window._reload_subjects()
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_1)
        assert window.service.active_session is None

        window._start_tracking(subject.id)
        active_session_id = window.service.active_session.id
        QTest.keyClick(window, Qt.Key_2)
        assert window.service.active_session.id == active_session_id
        assert not int(window.property("_jt_inline_dialog_count") or 0)
        window.service.stop_active_subject()

        goals = [window.service.add_todo_task(f"Goal {number}", "", None)
                 for number in range(1, 7)]
        window._switch_page(0)
        window._reload_tasks()
        opened = []
        window._open_goal = opened.append
        unrelated = window._todo_list.card_widget(goals[1].id)
        target = window._todo_list.card_widget(goals[4].id)
        unrelated.focus_btn.setFocus()
        QTest.mouseMove(unrelated.focus_btn, unrelated.focus_btn.rect().center())
        qt_app.processEvents()
        QTest.keyClick(unrelated.focus_btn, Qt.Key_5)
        assert target.hasFocus()
        assert not unrelated.focus_btn.hasFocus()
        assert unrelated.graphicsEffect() is None
        deadline = time.monotonic() + 2
        while not opened and time.monotonic() < deadline:
            QTest.qWait(20)
        assert opened == [goals[4].id]
        assert all(not window.service.get_goal(goal.id).is_focused for goal in goals)
    finally:
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
        assert not any(
            label.property("_jt_shortcut_badge")
            for label in subject_card.findChildren(QLabel)
        )
        goal_card = window._todo_list.itemWidget(window._todo_list.item(0))
        assert any(
            label.text() == "1"
            for label in goal_card.findChildren(QLabel)
        )
        assert window._graph_mode_buttons["bar"].text() == "Stacked Bar"
        assert window._graph_mode_buttons["agenda"].text() == "Agenda"
        assert not any(button._shortcut_hint for button in window._nav_buttons)
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


def test_goal_detail_digits_work_with_window_shortcuts_and_header_focus(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        goal = window.service.add_todo_task("Three milestones", "", None)
        milestones = [window.service.add_milestone(goal.id, f"Milestone {i}")
                      for i in range(1, 4)]
        window._switch_page(0)
        detail = GoalDetailDialog(window.service, goal.id, window)
        open_dialog(detail)
        qt_app.processEvents()
        assert detail._jt_inline_layer.isAncestorOf(qt_app.focusWidget())
        header_close = detail._jt_inline_layer.findChild(QPushButton, "jtInlineDialogClose")
        header_close.setFocus()
        qt_app.processEvents()
        for key in (Qt.Key_1, Qt.Key_2, Qt.Key_3):
            QTest.keyClick(header_close, key)
        assert [window.service.db.get_milestone(m.id).is_done for m in milestones] == [1, 1, 1]

        # Held digits must not repeatedly toggle a checkbox.
        repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier, "1", True, 1)
        qt_app.sendEvent(header_close, repeat)
        assert window.service.db.get_milestone(milestones[0].id).is_done == 1

        # Both tab directions stay in the panel rather than putting an accent
        # outline on another goal behind its backdrop.
        for key, modifiers in ((Qt.Key_Tab, Qt.NoModifier), (Qt.Key_Backtab, Qt.ShiftModifier)):
            for _ in range(12):
                QTest.keyClick(qt_app.focusWidget(), key, modifiers)
                assert detail._jt_inline_layer.isAncestorOf(qt_app.focusWidget())

        # The nested definition editor owns its text and never toggles the
        # underlying checklist, even when its header has focus.
        detail._edit_goal()
        qt_app.processEvents()
        editor = window.findChild(GoalDialog)
        editor.title_input.setFocus()
        QTest.keyClicks(editor.title_input, "123")
        assert editor.title_input.text().endswith("123")
        nested_close = editor._jt_inline_layer.findChild(QPushButton, "jtInlineDialogClose")
        nested_close.setFocus()
        QTest.keyClick(nested_close, Qt.Key_1)
        assert window.service.db.get_milestone(milestones[0].id).is_done == 1
        editor.reject()
        qt_app.processEvents()
        assert detail._jt_inline_layer.isAncestorOf(qt_app.focusWidget())
        header_close.setFocus()
        QTest.keyClick(header_close, Qt.Key_1)
        assert window.service.db.get_milestone(milestones[0].id).is_done == 0
        detail.reject()
        qt_app.processEvents()
    finally:
        window.close()
        qt_app.processEvents()


def test_switch_prompt_defaults_to_yes_for_enter(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._reload_subjects()
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()

        window._start_tracking(first.id)
        qt_app.processEvents()
        window._start_tracking(second.id)
        QTest.qWait(50)

        from PySide6.QtWidgets import QPushButton

        from jobtracker.ui.widgets.dialog_utils import InlineDialog

        box = window.findChild(InlineDialog)
        assert box is not None
        default_buttons = [
            button for button in box.findChildren(QPushButton) if button.isDefault()
        ]
        assert default_buttons, "switch prompt must have a default (Enter) button"
        assert default_buttons[0].text() in ("Yes", "&Yes")
        QTest.keyClick(default_buttons[0], Qt.Key_Return)
        QTest.qWait(50)
        qt_app.processEvents()
        assert window.service.active_subject.id == second.id
        window.service.stop_active_subject()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_graph_up_down_switch_views_without_hijacking_editors_or_other_pages(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        window._switch_page(2)
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_Down)
        assert window._graph_view_mode == "agenda"
        assert window._graph_mode_buttons["agenda"].hasFocus()
        QTest.keyClick(window._graph_mode_buttons["agenda"], Qt.Key_Up)
        assert window._graph_view_mode == "bar"
        QTest.keyClick(window, Qt.Key_2)
        assert window._graph_view_mode == "bar"
        QTest.keyClick(window, Qt.Key_1, Qt.ControlModifier)
        assert window._pages.currentIndex() == 2

        editor = QLineEdit(window)
        editor.show()
        editor.setFocus()
        qt_app.processEvents()
        QTest.keyClick(editor, Qt.Key_Down)
        assert window._graph_view_mode == "bar"
        editor.hide()

        # A nested checklist scrolls in place while Graphs remains behind it.
        goal = window.service.add_todo_task("Goal", "", None)
        window.service.add_milestone(goal.id, "Milestone")
        detail = GoalDetailDialog(window.service, goal.id, window)
        open_dialog(detail)
        qt_app.processEvents()
        detail._ms_list.setFocus()
        QTest.keyClick(detail._ms_list, Qt.Key_Down)
        assert window._graph_view_mode == "bar"
        detail.reject()
        qt_app.processEvents()
        window._switch_page(0)
        window.setFocus()
        QTest.keyClick(window, Qt.Key_Down)
        assert window._graph_view_mode == "bar"
    finally:
        window.close()
        qt_app.processEvents()


def test_weight_fullscreen_escape_and_navigation_restore_draft_and_sidebar(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        window._switch_page(3)
        view = window._weight_view
        view._select_whole(80)
        view._select_decimal(6)
        view._options_button.setChecked(True)
        view._options.show()
        window.activateWindow()
        for focus in (view._fullscreen_btn, view._span):
            view._fullscreen_btn.setChecked(True)
            focus.setFocus()
            qt_app.processEvents()
            assert not window._sidebar.isVisible()
            assert not view._entry.isVisible()
            QTest.keyClick(focus, Qt.Key_Escape)
            qt_app.processEvents()
            assert not view._fullscreen_btn.isChecked()
            assert window._sidebar.isVisible()
            assert view._entry.isVisible() and view._options.isVisible()
            assert view._value() == 80.6
        view._fullscreen_btn.setChecked(True)
        window._switch_page(1)
        qt_app.processEvents()
        assert window._sidebar.isVisible()
        window._switch_page(3)
        assert view._entry.isVisible() and not view._fullscreen_btn.isChecked()
        assert view._value() == 80.6
    finally:
        window.close()
        qt_app.processEvents()

