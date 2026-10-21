"""Offscreen native shell regressions on isolated databases."""
from datetime import (datetime, timedelta)
from jobtracker.services.tracker_service import (TrackerService)
from jobtracker.ui import (app as app_module)
from tests.support.native import (_window)
import pytest

def test_main_window_constructs_with_goals_and_graphs(database, monkeypatch):
    database.set_setting("graph_grouping", "weekly")
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._pages.count() == 4
        assert [button.text() for button in window._nav_buttons] == [
            "Goals", "Subjects", "Graphs", "Weight",
        ]
        # graph_grouping is a real preference again: an explicit bucket size is
        # honoured rather than wiped at launch.
        assert database.get_setting("graph_grouping", "") == "weekly"
        assert window._graph_grouping == "weekly"
        assert window._recurring_timer.isActive()
        assert window._pages.currentIndex() == 1
        assert window._graph_stack.count() == 2
        assert not hasattr(window, "_tasks_nav_btn")
        assert set(window._graph_mode_buttons) == {"bar", "agenda"}
        window._switch_page(2)
        qt_app.processEvents()
        assert (
            window._graph_mode_buttons["bar"].x()
            < window._graph_mode_buttons["agenda"].x()
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


@pytest.mark.parametrize("width", [600, 720])
def test_half_screen_keeps_card_actions_and_active_timer_inside_content(database, monkeypatch, width):
    qt_app, window = _window(database, monkeypatch)
    try:
        subject = window.service.add_subject("Long subject title " * 12, "#38BDF8", "")
        goal = window.service.add_todo_task("Long task title " * 12, "", None)
        window.service.add_milestone(goal.id, "An unfinished milestone")
        window._reload()
        window.resize(width, 950)
        qt_app.processEvents()

        for page, cards in ((0, window._todo_list), (1, window._subjects_list)):
            window._switch_page(page)
            qt_app.processEvents()
            card = cards.itemWidget(cards.item(0))
            assert card.width() <= cards.viewport().width()
            buttons = [card.focus_btn, card.edit_btn] if page == 0 else card._action_buttons
            assert all(card.rect().contains(button.geometry()) for button in buttons)
            cards.setFocus()

        window._showing_completed_goals = True
        window._reload_tasks()
        window._switch_page(0)
        qt_app.processEvents()
        assert window._pages.currentWidget().rect().contains(window._show_completed_btn.geometry())

        window._switch_page(1)
        assert window.service.start_subject(subject.id)
        window._reload_subjects()
        qt_app.processEvents()
        timer = window._timer
        assert timer.minimumSizeHint().width() <= timer.width()
        assert timer._name.geometry().right() < timer._clock.geometry().left()
        assert timer._clock.geometry().right() < timer._stop_btn.geometry().left()
        assert timer._stop_btn.parentWidget().rect().contains(timer._stop_btn.geometry())
    finally:
        window.service.stop_active_subject()
        window.close()
        qt_app.processEvents()

