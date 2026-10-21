"""Offscreen native subjects regressions on isolated databases."""
from datetime import (datetime, timedelta)
from PySide6.QtCore import (QEvent, Qt)
from PySide6.QtGui import (QKeyEvent)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QApplication, QListWidget, QMessageBox)
from jobtracker.ui.widgets.subject_dialog import (SubjectDialog)
from tests.support.native import (_window)

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


def test_colour_suggestions_ignore_archived_subjects(database, monkeypatch):
    """New-subject colour hints must only avoid ACTIVE subject colours."""
    from jobtracker.ui.widgets.subject_dialog import SubjectDialog

    qt_app, window = _window(database, monkeypatch)
    try:
        archived = window.service.add_subject("Old thing", "#22C55E", "")
        window.service.add_subject("Active thing", "#3B82F6", "")
        window.service.archive_subject(archived.id)

        window._new_subject()
        qt_app.processEvents()
        dialog = window.findChild(SubjectDialog)
        assert dialog is not None
        # The archived subject's colour is not treated as taken.
        assert "#22C55E" not in dialog._existing_colors
        assert "#3B82F6" in dialog._existing_colors
        dialog.reject()
        qt_app.processEvents()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_switch_prompt_ignores_a_stale_premise(database, monkeypatch):
    """Answering Yes must not act if the timer state changed meanwhile."""
    import jobtracker.ui.subjects_mixin as subjects_mixin_module

    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._reload_subjects()
        window._start_tracking(first.id)
        qt_app.processEvents()

        # Capture the prompt's callback instead of answering immediately.
        pending: list = []
        monkeypatch.setattr(
            subjects_mixin_module,
            "question",
            lambda _p, _t, _x, on_finished, **_kw: pending.append(on_finished),
        )
        window._start_tracking(second.id)
        assert pending, "expected a switch prompt"

        # The user stops tracking while the prompt is still open, then says Yes.
        window.service.stop_active_subject()
        pending[0](QMessageBox.Yes)
        qt_app.processEvents()

        # Nothing was started from the stale prompt.
        assert window.service.active_session is None
        assert window.service.db.get_open_sessions() == []
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_sidebar_shortcuts_and_timer_resume_and_discard_notice(
    database, monkeypatch
):
    """D1-B sidebar page shortcuts, D2-A idle resume button, and L3-A <30s discard notice."""
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Deep Work", "#38BDF8", "")
        window._reload_subjects()
        qt_app.processEvents()

        # Sidebar page navigation (⌘1–⌘4) keeps Goals first (0), Subjects (1), Graphs (2), Weight (3).
        window._navigate_to_page(1)
        assert window._pages.currentIndex() == 1
        assert window._nav_buttons[1].isChecked()

        # Idle bar shows Resume button for the available subject.
        assert not window._timer._resume_btn.isHidden()
        assert "Deep Work" in window._timer._resume_btn.text()

        # Clicking Resume starts tracking that subject.
        window._timer._resume_btn.click()
        qt_app.processEvents()
        assert window.service.active_subject is not None
        assert window.service.active_subject.id == subject.id

        # Stopping a session under 30s discards it and shows the L3-A transient notice.
        window._stop_tracking()
        qt_app.processEvents()
        assert window.service.active_session is None
        assert "30s" in window._timer._idle_label.text()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


