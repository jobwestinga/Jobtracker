"""Native keyboard controls and device-local motion preferences."""
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from jobtracker.core.themes import get_tokens
from jobtracker.sync import state
from jobtracker.sync.service import SyncedTrackerService
from jobtracker.ui.widgets.dialog_utils import InlineDialog, open_dialog
from jobtracker.ui.widgets.goal_dialog import GoalDialog
from jobtracker.ui.widgets.session_dialog import DurationDialog, SessionDialog
from jobtracker.ui.widgets.settings_dialog import SettingsDialog
from jobtracker.ui.widgets.subject_dialog import SubjectDialog
from jobtracker.ui.widgets.subject_item import SubjectItemWidget
from jobtracker.ui.widgets.todo_task_item import TodoTaskItemWidget, _StarBurstOverlay
from tests.support.native import _application, _window


@pytest.fixture
def accessible_window(database, monkeypatch):
    app, window = _window(database, monkeypatch)
    yield app, window
    for dialog in window.findChildren(InlineDialog):
        dialog.reject()
    QTest.qWait(5)
    if window.service.active_session is not None:
        start = datetime.fromisoformat(window.service.active_session.start_time)
        window.service.stop_active_subject(start + timedelta(seconds=31))
    window.close()
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_Space])
def test_focused_subject_keyboard_starts_only_that_subject(service, key):
    app = _application()
    first = service.add_subject("First", "#3B82F6", "")
    second = service.add_subject("Second", "#22C55E", "")
    card = SubjectItemWidget(second, get_tokens())
    card.show()
    card.activateWindow()
    card.setFocus()
    app.processEvents()
    chosen = []
    card.start_requested.connect(chosen.append)
    QTest.keyClick(card, key)
    assert chosen == [second.id] and first.id not in chosen
    assert "Second" in card.accessibleName()
    card.close()
    card.deleteLater()


def test_subject_action_buttons_are_keyboard_reachable_without_starting_timer(service):
    app = _application()
    subject = service.add_subject("Keyboard subject", "#3B82F6", "")
    card = SubjectItemWidget(subject, get_tokens())
    card.show()
    card.activateWindow()
    edits, starts = [], []
    card.edit_requested.connect(edits.append)
    card.start_requested.connect(starts.append)
    button = card._action_buttons[0]
    assert button.focusPolicy() == Qt.StrongFocus
    button.setFocus()
    app.processEvents()
    QTest.keyClick(button, Qt.Key_Space)
    assert edits == [subject.id] and starts == []
    assert "Keyboard subject" in button.accessibleName()
    assert ":focus" in button.styleSheet()
    card.close()
    card.deleteLater()


def test_archived_keyboard_card_cannot_start_tracking(service):
    app = _application()
    subject = service.add_subject("Archived", "#3B82F6", "")
    card = SubjectItemWidget(subject, get_tokens(), is_archived=True)
    card.show()
    card.activateWindow()
    card.setFocus()
    app.processEvents()
    starts = []
    card.start_requested.connect(starts.append)
    QTest.keyClick(card, Qt.Key_Return)
    assert starts == []
    assert [button.text() for button in card._action_buttons] == ["Unarchive", "Sessions", "Delete"]
    card.close()
    card.deleteLater()


def test_goal_keyboard_open_and_glyph_labels_preserve_milestone_gate(service):
    app = _application()
    goal = service.add_todo_task("Accessible goal", "", None)
    card = TodoTaskItemWidget(goal, get_tokens(), progress=(0, 1))
    card.show()
    card.activateWindow()
    card.setFocus()
    app.processEvents()
    opened, completed = [], []
    card.open_requested.connect(opened.append)
    card.complete_requested.connect(completed.append)
    QTest.keyClick(card, Qt.Key_Return)
    assert opened == [goal.id]
    assert "Accessible goal" in card.check_btn.accessibleName()
    assert "milestones" in card.check_btn.accessibleDescription()
    card.check_btn.setFocus()
    QTest.keyClick(card.check_btn, Qt.Key_Space)
    assert completed == [] and opened == [goal.id]
    assert "Focus this week" in card.focus_btn.accessibleName()
    if hasattr(card, "_shake_anim"):
        card._shake_anim.stop()
    card.close()
    card.deleteLater()


def test_editor_inputs_and_color_only_controls_have_names_and_buddies(service):
    _application()
    subject = SubjectDialog()
    goal = GoalDialog()
    duration = DurationDialog()
    session = SessionDialog(service=service)
    settings = SettingsDialog(service=service)
    controls = [subject.name_input, subject.notes_input, goal.title_input, goal.desc_input,
                duration.hours_input, duration.minutes_input, session.start_edit, session.end_edit,
                settings.sync_url_input, settings.sync_token_input, settings.day_start_combo,
                settings.theme_combo]
    assert all(control.accessibleName() for control in controls)
    assert all(button.accessibleName() for button in subject._swatch_btns)
    assert all(button.accessibleName() for button in duration.chunk_buttons.values())
    assert any(label.buddy() is subject.name_input for label in subject.findChildren(type(subject.color_preview)))
    for editor in (subject, goal, duration, session, settings):
        editor.reject()
        editor.deleteLater()


def test_window_navigation_settings_and_sync_have_accessible_names(accessible_window):
    _app, window = accessible_window
    assert all(button.accessibleName().endswith("tab") for button in window._nav_buttons)
    gears = [button for button in window.findChildren(QPushButton) if button.text() == "⚙"]
    assert gears and all(button.accessibleName() == "Settings" for button in gears)
    assert "Synchronization" in window._sync_status.accessibleName()
    assert window._sync_status.action.accessibleName() == "Open sync settings"
    assert "Stop" in window._timer._stop_btn.accessibleName()


def test_reduce_motion_is_saved_locally_and_preserves_timer_and_completion(accessible_window):
    app, window = accessible_window
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor, window._finish_settings)
    app.processEvents()
    editor.reduce_motion_check.setChecked(True)
    editor.accept()
    QTest.qWait(5)
    app.processEvents()
    assert window.service.get_setting("ui_reduce_motion") == "1"
    assert window.property("_jt_reduce_motion")
    assert not window._fx_bg._timer.isActive()
    window._on_app_state_changed(Qt.ApplicationActive)
    assert not window._fx_bg._timer.isActive()
    subject = window.service.add_subject("Still timed", "#111111", "")
    window.service.start_subject(subject.id, datetime.now() - timedelta(minutes=1))
    window._reload_subjects()
    window._timer.pulse_stop_button()
    assert window._timer._tick.isActive() and window._timer._stop_btn.graphicsEffect() is None
    goal = window.service.add_todo_task("Complete immediately", "", None)
    window._reload_tasks()
    window._switch_page(0)
    card = window._todo_list.card_widget(goal.id)
    card.check_btn.click()
    assert window.service.get_goal(goal.id).is_completed
    assert window._todo_list._remove_anims == {}
    assert not window.findChildren(_StarBurstOverlay)


def test_motion_preference_is_not_queued_to_other_devices(database):
    _application()
    service = SyncedTrackerService(database)
    service.set_setting("ui_reduce_motion", "1")
    assert state.pending_count(database.connection) == 0
    assert service.get_setting("ui_reduce_motion") == "1"


def test_settings_fits_small_owner_and_keeps_save_reachable_in_footer(accessible_window):
    app, window = accessible_window
    window.resize(560, 760)
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor)
    app.processEvents()
    panel = editor._jt_inline_layer._panel
    assert window.rect().contains(panel.geometry())
    save = next(button for button in editor.findChildren(QPushButton) if button.text() == "Save")
    editor.sync_url_input.setFocus()
    app.processEvents()
    for _ in range(10):
        if save.hasFocus():
            break
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
        app.processEvents()
    assert save.hasFocus()
    focused_rect = save.rect().translated(save.mapTo(editor, save.rect().topLeft()))
    assert editor.rect().contains(focused_rect)
    assert not editor.scroll_area.isAncestorOf(save)


def test_open_settings_tracks_owner_resize_and_preserves_draft(accessible_window):
    app, window = accessible_window
    window.resize(680, 920)
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor)
    app.processEvents()
    editor.sync_url_input.setText("https://draft.example.invalid")
    editor.sync_token_input.setText("unfinished-token")
    window.resize(560, 760)
    app.processEvents()
    assert editor.height() <= 650
    assert window.rect().contains(editor._jt_inline_layer._panel.geometry())
    assert editor.sync_url_input.text() == "https://draft.example.invalid"
    assert editor.sync_token_input.text() == "unfinished-token"
    editor.sync_token_input.setFocus()
    app.processEvents()
    viewport = editor.scroll_area.viewport()
    focused_rect = editor.sync_token_input.rect().translated(editor.sync_token_input.mapTo(viewport, editor.sync_token_input.rect().topLeft()))
    assert viewport.rect().contains(focused_rect)


@pytest.mark.parametrize("theme", ["Ocean", "OLED Midnight"])
def test_settings_scroll_background_follows_theme_preview(accessible_window, theme):
    app, window = accessible_window
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor)
    app.processEvents()
    editor._select_preset(theme)
    app.processEvents()
    image = editor.scroll_area.widget().grab().toImage()
    assert image.pixelColor(5, 5).name().lower() == get_tokens(editor._fx, editor._palette)["BG_SECONDARY"].lower()
