"""Native workflow races use throwaway services and real offscreen widgets."""

from datetime import datetime, timedelta
import gc
import json
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QVariantAnimation, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox

from jobtracker.ui.widgets.active_timer import ActiveTimerWidget
from jobtracker.ui.widgets.dialog_utils import InlineDialog, open_dialog
from jobtracker.ui.widgets.settings_dialog import SettingsDialog
from jobtracker.ui.widgets.subject_dialog import SubjectDialog
from jobtracker.ui.widgets.reorderable_list import ReorderableCardList
from jobtracker.ui.controllers.keyboard import KeyboardController
from tests.support.native import _application, _window


@pytest.fixture
def native_window(database, monkeypatch):
    app, window = _window(database, monkeypatch)
    yield app, window
    for dialog in window.findChildren(InlineDialog):
        dialog.reject()
    QTest.qWait(10)
    app.processEvents()
    for timer in window.findChildren(QTimer):
        timer.stop()
    for animation in window.findChildren(QVariantAnimation):
        animation.stop()
    if window.service.active_session is not None:
        start = datetime.fromisoformat(window.service.active_session.start_time)
        window.service.stop_active_subject(start + timedelta(seconds=31))
    window.close()
    app.processEvents()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_archived_subject_edit_opens_and_preserves_archive(native_window):
    app, window = native_window
    subject = window.service.add_subject("Archived", "#3B82F6", "old notes")
    window.service.archive_subject(subject.id)
    window._toggle_archived_view()
    window._edit_subject(subject.id)
    QTest.qWait(10)
    app.processEvents()
    editor = window.findChild(SubjectDialog)
    assert editor is not None and editor.name_input.text() == "Archived"
    editor.name_input.setText("Renamed archive")
    editor.accept()
    QTest.qWait(10)
    app.processEvents()
    saved = next(s for s in window.service.get_all_subjects_including_archived() if s.id == subject.id)
    assert saved.name == "Renamed archive" and saved.is_archived


def test_old_switch_prompt_cannot_stop_restarted_same_subject(native_window, monkeypatch):
    from jobtracker.ui import subjects_mixin

    app, window = native_window
    first = window.service.add_subject("First", "#111111", "")
    second = window.service.add_subject("Second", "#222222", "")
    start = datetime.now() - timedelta(minutes=2)
    window.service.start_subject(first.id, start)
    window._reload_subjects()
    callbacks = []
    monkeypatch.setattr(subjects_mixin, "question", lambda _p, _t, _s, callback, **_kw: callbacks.append(callback))
    window._confirm_switch(second.id)
    old_uid = window.service.active_session.uid
    window.service.stop_active_subject(start + timedelta(minutes=1))
    window.service.start_subject(first.id)
    restarted = window.service.active_session
    assert restarted.uid != old_uid
    callbacks[0](QMessageBox.Yes)
    app.processEvents()
    assert window.service.active_session.uid == restarted.uid
    assert window.service.active_subject.id == first.id


@pytest.mark.parametrize("change", ["navigation", "draft"])
def test_delayed_goal_shortcut_respects_new_context(native_window, change):
    app, window = native_window
    goal = window.service.add_todo_task("Goal", "", None)
    window._switch_page(0)
    window.activateWindow()
    window.setFocus()
    app.processEvents()
    opened = []
    window._open_goal = opened.append
    window._open_goal_after_shortcut_feedback(goal.id)
    if change == "navigation":
        window._switch_page(1)
    else:
        editor = SubjectDialog(window)
        open_dialog(editor)
        app.processEvents()
        editor.name_input.setText("Unfinished draft")
    QTest.qWait(170)
    assert opened == []
    if change == "draft":
        assert editor.name_input.text() == "Unfinished draft"
        editor.reject()
        app.processEvents()


@pytest.mark.parametrize("dismissal", ["reject", "escape"])
def test_settings_rejection_always_reverts_theme_preview(native_window, dismissal):
    app, window = native_window
    original_style = app.styleSheet()
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor)
    app.processEvents()
    editor._select_palette("Light" if editor._palette != "Light" else "Dark")
    assert app.styleSheet() != original_style
    if dismissal == "escape":
        QTest.keyClick(editor.sync_url_input, Qt.Key_Escape)
    else:
        editor.reject()
    app.processEvents()
    assert app.styleSheet() == original_style


@pytest.mark.parametrize("name,palette", [("OLED Midnight", "Obsidian"), ("Carbon Dark", "Dark")])
def test_dark_theme_preset_preview_cancel_and_save_preserve_weight_draft(native_window, name, palette):
    from jobtracker.core.themes import get_tokens

    app, window = native_window
    original_style, original_tokens = app.styleSheet(), window._tokens
    window._weight_view._select_whole(80)
    window._weight_view._select_decimal(6)
    original_palette = window.service.get_setting("theme_palette", "Ocean")

    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor, window._finish_settings)
    app.processEvents()
    editor._preset_btns[name].click()
    tokens = get_tokens("Clean", palette)
    assert window._weight_view._chart._tokens == tokens
    assert all(button._tokens == tokens for button in window._nav_buttons)
    assert window.service.get_setting("theme_palette", "Ocean") == original_palette
    editor.reject()
    app.processEvents()
    assert app.styleSheet() == original_style
    assert window._weight_view._chart._tokens == original_tokens
    assert all(button._tokens == original_tokens for button in window._nav_buttons)

    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor, window._finish_settings)
    app.processEvents()
    editor._preset_btns[name].click()
    editor.accept()
    app.processEvents()
    assert window.service.get_setting("theme_fx") == "Clean"
    assert window.service.get_setting("theme_palette") == palette
    assert window._tokens == tokens
    assert window._weight_view._value() == 80.6


@pytest.mark.parametrize("boundary", ["06:30", "23:45"])
def test_unrelated_settings_save_preserves_exact_day_boundary(service, monkeypatch, boundary):
    _application()
    service.set_day_start(boundary)
    editor = SettingsDialog(service=service)
    monkeypatch.setattr(editor, "_persist_sync_settings", lambda: None)
    assert editor.get_settings()["day_start_time"] == boundary
    editor.day_start_combo.setCurrentIndex(editor.day_start_combo.findData(7))
    assert editor.get_settings()["day_start_time"] == "07:00"
    editor.reject()


def test_repeated_stop_guidance_does_not_use_destroyed_effect(service, monkeypatch):
    app = _application()
    subject = service.add_subject("Timer", "#111111", "")
    service.start_subject(subject.id)
    timer = ActiveTimerWidget()
    timer.set_active(subject, service.active_session)
    timer.show()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    timer.pulse_stop_button()
    timer.pulse_stop_button()
    # The unified Qt animation clock can start its first frame late under CI
    # load. Wait for completion rather than assuming duration + 30 ms is enough.
    deadline = time.monotonic() + 2
    while timer._stop_btn.graphicsEffect() is not None and time.monotonic() < deadline:
        QTest.qWait(20)
    app.processEvents()
    assert errors == []
    assert timer._stop_btn.graphicsEffect() is None
    timer.clear()
    timer.close()


def test_goal_completion_is_durable_before_feedback_and_double_click_is_single_action(native_window, monkeypatch):
    app, window = native_window
    goal = window.service.add_todo_task("Finish once", "", None)
    window._reload_tasks()
    window._switch_page(0)
    app.processEvents()
    completions = []
    original = window.service.complete_goal

    def complete(goal_id):
        completions.append(goal_id)
        return original(goal_id)

    monkeypatch.setattr(window.service, "complete_goal", complete)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    window._complete_goal(goal.id)
    window._complete_goal(goal.id)
    assert window.service.get_goal(goal.id).is_completed
    assert completions == [goal.id]
    # An unrelated reload can replace the animating card before its last tick.
    window._reload_tasks()
    QTest.qWait(360)
    app.processEvents()
    assert errors == [] and completions == [goal.id]
    assert goal.id not in window._todo_list.ordered_ids()


def test_imported_theme_survives_settings_rejection(native_window, monkeypatch, tmp_path):
    from jobtracker.ui.widgets import settings_dialog

    app, window = native_window
    editor = SettingsDialog(window, service=window.service)
    open_dialog(editor)
    app.processEvents()
    imported_palette = "Light" if editor._palette != "Light" else "Dark"
    editor._select_palette(imported_palette)
    backup = window.service.export_data()
    backup["settings"] = [row for row in backup["settings"] if row["key"] != "theme_palette"]
    backup["settings"].append({"key": "theme_palette", "value": imported_palette})
    path = tmp_path / "restored.json"
    path.write_text(json.dumps(backup))
    monkeypatch.setattr(
        settings_dialog, "information",
        lambda _p, _title, _text, on_finished: on_finished(QMessageBox.Ok),
    )
    editor._finish_import(str(path), QMessageBox.Yes)
    app.processEvents()
    assert window._palette == imported_palette
    assert window.service.get_setting("theme_palette") == imported_palette
    from jobtracker.core.themes import get_tokens
    from jobtracker.ui.styles import build_stylesheet

    assert app.styleSheet() == build_stylesheet(get_tokens(window._fx, imported_palette))


def test_deferred_ui_action_is_cancelled_when_owning_widget_is_destroyed():
    app = _application()
    owner = QFrame()
    called = []
    KeyboardController(owner)._queue_ui_action(lambda: called.append("stale"))
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    assert called == []


@pytest.mark.parametrize("interruption", ["duplicate", "clear", "replace"])
def test_card_removal_survives_repeated_input_and_refresh(monkeypatch, interruption):
    app = _application()
    cards = ReorderableCardList()
    card = QLabel("Completed card")
    card.setFixedHeight(60)
    cards.add_card(1, card)
    cards.resize(400, 180)
    cards.show()
    app.processEvents()
    finished = []
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    cards.animate_remove(1, lambda: finished.append(1), duration_ms=120)
    if interruption == "duplicate":
        cards.animate_remove(1, lambda: finished.append(2), duration_ms=120)
    elif interruption == "clear":
        cards.clear_cards()
    else:
        cards.sync_cards([1], {1: "edited"}, lambda _id: QLabel("Replacement"), lambda *_: None)
    QTest.qWait(150)
    app.processEvents()
    assert errors == []
    assert finished == ([1] if interruption == "duplicate" else [])
    assert cards.count() == (1 if interruption == "replace" else 0)
    assert cards._remove_anims == {}
    cards.clear_cards()
    cards.close()
    cards.deleteLater()


@pytest.mark.parametrize("feedback", ["timer", "remove", "cancel_remove"])
def test_animation_lifetimes_survive_reuse_gc_and_owner_destruction(service, monkeypatch, feedback):
    """Exercise the late-suite Qt destruction path with frequent Python GC."""
    app = _application()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    if feedback == "timer":
        subject = service.add_subject("Synthetic timer", "#111111", "")
        service.start_subject(subject.id)
        owner = ActiveTimerWidget()
        owner.set_active(subject, service.active_session)
    else:
        owner = ReorderableCardList()
    owner.resize(400, 180)
    owner.show()
    app.processEvents()
    completed = []
    for index in range(60):
        if feedback == "timer":
            owner.pulse_stop_button()
            owner.pulse_stop_button()
            owner._stop_pulse_anim.setCurrentTime(owner._stop_pulse_anim.duration())
            assert owner._stop_btn.graphicsEffect() is None
        else:
            card = QLabel(f"Card {index}")
            card.setFixedHeight(60)
            owner.add_card(index, card)
            owner.animate_remove(index, lambda: completed.append(True))
            if feedback == "cancel_remove":
                owner.clear_cards()
            else:
                owner._remove_anims[index].setCurrentTime(owner._remove_anims[index].duration())
            assert owner.count() == 0
        # Repeated feedback must not accumulate animation wrappers or keep
        # callbacks for cards already replaced by a refresh.
        assert len(owner.findChildren(QVariantAnimation)) <= 1
        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    assert len(completed) == (60 if feedback == "remove" else 0)
    owner.close()
    owner.deleteLater()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
    assert errors == []
