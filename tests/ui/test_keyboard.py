"""Offscreen native keyboard regressions on isolated databases."""
from datetime import (datetime, timedelta)
from PySide6.QtCore import (QSize, Qt)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QLineEdit, QListWidget, QMessageBox)
from jobtracker.ui.widgets.dialog_utils import (open_dialog)
from jobtracker.ui.widgets.goal_dialog import (GoalDetailDialog)
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
        ):
            QTest.keyClick(window, key)
            assert window._graph_view_mode == mode
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


def test_switch_prompt_quick_confirms_with_repeated_number(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._reload_subjects()
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()

        QTest.keyClick(window, Qt.Key_1)
        qt_app.processEvents()
        assert window.service.active_subject.id == first.id

        QTest.keyClick(window, Qt.Key_2)  # opens the switch prompt
        QTest.qWait(50)
        assert int(window.property("_jt_inline_dialog_count") or 0) == 1

        QTest.keyClick(window, Qt.Key_2)  # same number again = Yes
        QTest.qWait(50)
        qt_app.processEvents()
        assert window.service.active_subject.id == second.id
        assert int(window.property("_jt_inline_dialog_count") or 0) == 0
        assert len(window.service.db.get_open_sessions()) == 1

        window.service.stop_active_subject()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_switch_prompt_defaults_to_yes_for_enter(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._reload_subjects()
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()

        QTest.keyClick(window, Qt.Key_1)
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
        box.done(int(QMessageBox.Yes))
        QTest.qWait(50)
        qt_app.processEvents()
        assert window.service.active_subject.id == second.id
        window.service.stop_active_subject()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_graph_number_shortcuts_cover_only_the_two_views(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        window._switch_page(2)
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_2)
        assert window._graph_view_mode == "agenda"
        # 3 used to be the heatmap; it must now do nothing rather than break.
        QTest.keyClick(window, Qt.Key_3)
        assert window._graph_view_mode == "agenda"
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()

