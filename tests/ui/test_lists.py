"""Offscreen native lists regressions on isolated databases."""
from PySide6.QtCore import (QPoint, Qt)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel)
from jobtracker.ui.widgets.reorderable_list import (ReorderableCardList)
from tests.support.native import (_application, _window)

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

