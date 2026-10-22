"""Offscreen native sessions regressions on isolated databases."""
from datetime import (date, datetime, timedelta)
import time

import pytest
from PySide6.QtCore import (QEvent, Qt)
from PySide6.QtGui import (QKeyEvent)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QMessageBox, QPushButton)
from tests.support.native import (_application, _day_with_two_sessions)

def test_session_dialog_subject_picker_only_when_editing(service):
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    _application()
    first = service.add_subject("General", "#111111", "")
    second = service.add_subject("Specific", "#222222", "")
    session = service.add_session(
        first.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )

    # Add mode: no subject picker.
    add_dialog = SessionDialog(None)
    assert add_dialog.subject_combo is None
    assert add_dialog.get_data()["subject_id"] is None
    add_dialog.close()

    # Edit mode with a service: picker present, current subject preselected.
    edit_dialog = SessionDialog(
        None, session, service=service, current_subject_id=first.id
    )
    combo = edit_dialog.subject_combo
    assert combo is not None
    names = [combo.itemText(i) for i in range(combo.count())]
    assert "General" in names and "Specific" in names
    assert combo.currentData() == first.id

    combo.setCurrentIndex(combo.findData(second.id))
    assert edit_dialog.get_data()["subject_id"] == second.id
    edit_dialog.close()


def test_manage_sessions_edit_moves_session_to_chosen_subject(service):
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app = _application()
    first = service.add_subject("General", "#111111", "")
    second = service.add_subject("Specific", "#222222", "")
    session = service.add_session(
        first.id,
        datetime(2026, 6, 20, 9, 0),
        datetime(2026, 6, 20, 10, 0),
    )

    manager = ManageSessionsDialog(first.id, service)
    manager.show()
    qt_app.processEvents()
    assert manager._list.count() == 1

    # Drive the real Edit path (shared session_list.edit_session).
    manager._edit()
    QTest.qWait(30)
    qt_app.processEvents()
    edit_dialog = manager.findChild(SessionDialog)
    assert edit_dialog is not None and edit_dialog.session.id == session.id
    edit_dialog.subject_combo.setCurrentIndex(
        edit_dialog.subject_combo.findData(second.id)
    )
    edit_dialog.accept()
    QTest.qWait(30)
    qt_app.processEvents()

    moved = service.get_sessions_for_subject(second.id)
    assert len(moved) == 1
    assert moved[0].id == session.id
    assert moved[0].duration_seconds == 3600
    assert service.get_sessions_for_subject(first.id) == []
    # The source list no longer shows the moved session.
    assert manager._list.count() == 0 or manager._list.isHidden()
    manager.close()


def test_manage_sessions_duplicate_buttons_copy_and_select_the_copy(service):
    from PySide6.QtWidgets import QPushButton

    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Routine", "#111111", "")
    start = (datetime.now() - timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    original = service.add_session(
        subject.id, start, start + timedelta(hours=2)
    )

    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    qt_app.processEvents()

    labels = {b.text() for b in manager.findChildren(QPushButton)}
    assert "⧉ Duplicate to Today" in labels and "⧉ +1 Day" in labels
    assert manager._list.count() == 1

    # "+1 Day" copies forward and leaves the COPY selected, so a second click
    # walks another day forward rather than re-copying the original.
    manager._duplicate("next_day")
    qt_app.processEvents()
    assert manager._list.count() == 2
    selected = manager._list.selected_session()
    assert selected["session_id"] != original.id

    manager._duplicate("next_day")
    qt_app.processEvents()
    assert manager._list.count() == 3
    days = sorted(
        datetime.fromisoformat(s.start_time).date()
        for s in service.get_sessions_for_subject(subject.id)
    )
    assert days == [start.date() + timedelta(days=n) for n in (0, 1, 2)]

    # "Duplicate to Today" lands on today, same clock time, same duration.
    manager._duplicate("today")
    qt_app.processEvents()
    copy = manager._list.selected_session()
    copy_start = datetime.fromisoformat(copy["start_time"])
    assert copy_start.time() == start.time()
    assert copy["duration_seconds"] == original.duration_seconds
    manager.close()


def test_day_dialog_rows_carry_session_identity_and_totals(service):
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog

    qt_app = _application()
    day, _a, _b, first, second = _day_with_two_sessions(service)

    dialog = DaySessionsDialog(service, day)
    dialog.show()
    qt_app.processEvents()

    assert dialog._list.count() == 2
    ids = [
        dialog._list.item(row).data(Qt.UserRole)["session_id"]
        for row in range(dialog._list.count())
    ]
    assert ids == [first.id, second.id]  # chronological
    assert "3h 0m" in dialog._total_lbl.text()
    per_subject = dialog._per_subject_lbl.text()
    assert "Physics" in per_subject and "Maths" in per_subject
    dialog.close()


def test_day_dialog_edits_the_selected_session_not_an_arbitrary_one(service):
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app = _application()
    day, _a, subject_b, _first, second = _day_with_two_sessions(service)

    dialog = DaySessionsDialog(service, day)
    dialog.show()
    qt_app.processEvents()

    dialog._list.setCurrentRow(1)  # the Maths session
    dialog._edit_selected()
    qt_app.processEvents()

    editor = dialog.findChild(SessionDialog)
    assert editor is not None
    # The editor opened on the row that was selected.
    assert editor.session.id == second.id
    assert editor.subject_combo.currentData() == subject_b.id
    editor.reject()
    qt_app.processEvents()
    dialog.close()


def test_day_dialog_duplicate_and_delete_act_on_the_selection(service, monkeypatch):
    from jobtracker.ui.widgets import session_list as actions_module
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog

    qt_app = _application()
    day, subject_a, _b, first, _second = _day_with_two_sessions(service)
    monkeypatch.setattr(
        actions_module, "information", lambda *_args, **_kwargs: None
    )

    dialog = DaySessionsDialog(service, day)
    dialog.show()
    qt_app.processEvents()

    dialog._list.setCurrentRow(0)  # Physics
    dialog._duplicate_selected()
    qt_app.processEvents()
    copies = service.get_sessions_for_subject(subject_a.id)
    assert len(copies) == 2
    assert {c.duration_seconds for c in copies} == {3600}

    # Delete the original, confirmed.
    monkeypatch.setattr(
        actions_module,
        "question",
        lambda _parent, _title, _text, on_finished, **_kw: on_finished(
            QMessageBox.Yes
        ),
    )
    dialog._reload(select_session_id=first.id)
    dialog._delete_selected()
    qt_app.processEvents()
    assert service.get_session(first.id) is None
    dialog.close()


def test_day_dialog_has_no_history_button_and_manager_still_preselects(service):
    from PySide6.QtWidgets import QPushButton

    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    day, subject_a, _b, first, _second = _day_with_two_sessions(service)
    # Older and newer sessions exist, so "newest first" would pick the wrong one.
    older = datetime.fromisoformat(first.start_time) - timedelta(days=3)
    service.add_session(subject_a.id, older, older + timedelta(hours=1))
    newer = datetime.fromisoformat(first.start_time) + timedelta(days=1)
    service.add_session(subject_a.id, newer, newer + timedelta(hours=1))

    dialog = DaySessionsDialog(service, day)
    dialog.show()
    qt_app.processEvents()
    labels = {b.text() for b in dialog.findChildren(QPushButton)}
    assert not any("history" in label.lower() for label in labels)
    assert "⧉ Duplicate to Today" in labels
    dialog.close()

    # Opening the subject manager directly still preselects the right session.
    manager = ManageSessionsDialog(
        subject_a.id, service, select_session_id=first.id
    )
    manager.show()
    qt_app.processEvents()
    assert manager._list.selected_session()["session_id"] == first.id
    manager.close()


def test_session_dialog_has_no_notes_and_move_buttons_shift_both_ends(service):
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 6, 20, 14, 0)
    session = service.add_session(subject.id, start, start + timedelta(hours=2))

    dialog = SessionDialog(
        None, session, service=service, current_subject_id=subject.id
    )
    assert not hasattr(dialog, "notes_input")
    assert "note" not in dialog.get_data()

    dialog._shift(-3600)
    dialog._shift(-900)
    data = dialog.get_data()
    assert data["start_time"] == start - timedelta(hours=1, minutes=15)
    assert data["end_time"] == start + timedelta(minutes=45)
    # Duration is untouched by a move.
    assert data["end_time"] - data["start_time"] == timedelta(hours=2)
    dialog.close()


def test_move_row_exposes_duration_and_day_steps():
    from jobtracker.ui.widgets.session_list import MOVE_STEPS, build_move_row

    _application()
    assert [seconds for _label, seconds in MOVE_STEPS] == [-86400, -3600, -900, 900, 3600, 86400]
    seen: list[int] = []
    row = build_move_row(seen.append)
    buttons = [row.itemAt(i).widget() for i in range(row.count())]
    assert [b.text() for b in buttons] == ["−1 day", "−1h", "−15m", "+15m", "+1h", "+1 day"]
    for button in buttons:
        button.click()
    assert seen == [-86400, -3600, -900, 900, 3600, 86400]


def test_duration_dialog_caps_minutes_and_rejects_zero(service, monkeypatch):
    from jobtracker.ui.widgets import session_dialog as session_dialog_module
    from jobtracker.ui.widgets.session_dialog import DurationDialog

    _application()
    dialog = DurationDialog()
    assert dialog.minutes_input.maximum() == 59
    dialog.minutes_input.setValue(90)
    assert dialog.minutes_input.value() == 59  # clamped, never a full hour

    dialog.hours_input.setValue(3)
    dialog.minutes_input.setValue(7)
    assert dialog.total_minutes() == 187

    # Zero duration is refused instead of being silently dropped by the 30s rule.
    warned: list[str] = []
    monkeypatch.setattr(
        session_dialog_module,
        "warning",
        lambda _parent, title, _text, **_kw: warned.append(title),
    )
    dialog.hours_input.setValue(0)
    dialog.minutes_input.setValue(0)
    dialog._validate_and_accept()
    assert warned == ["Duration Too Short"]
    dialog.close()


def test_duration_chunks_toggle_once_and_show_live_total():
    from jobtracker.ui.widgets.session_dialog import DurationDialog

    _application()
    dialog = DurationDialog()
    assert not dialog.confirm_btn.isEnabled()
    assert [button.text() for button in dialog.chunk_buttons.values()] == [
        "4h", "2h", "1h", "30m", "15m", "10m", "5m",
    ]
    for minutes, expected in ((120, 120), (30, 150), (15, 165), (5, 170)):
        dialog.chunk_buttons[minutes].click()
        assert dialog.total_minutes() == expected
        assert f"({expected} minutes)" in dialog.total_label.text()
    assert (dialog.hours_input.value(), dialog.minutes_input.value()) == (2, 50)
    assert dialog.confirm_btn.isEnabled()

    dialog.chunk_buttons[120].click()
    assert dialog.total_minutes() == 50
    dialog.chunk_buttons[120].click()
    assert dialog.total_minutes() == 170  # each chunk is used at most once
    for minutes in (120, 30, 15, 5):
        dialog.chunk_buttons[minutes].click()
    assert dialog.total_minutes() == 0
    assert not dialog.confirm_btn.isEnabled()
    dialog.close()


def test_duration_exact_entry_and_chunks_remain_synchronized():
    from jobtracker.ui.widgets.session_dialog import DurationDialog

    _application()
    dialog = DurationDialog()
    dialog.chunk_buttons[120].click()
    dialog.chunk_buttons[30].click()
    dialog.hours_input.setValue(1)
    assert dialog.total_minutes() == 90
    assert {m for m, b in dialog.chunk_buttons.items() if b.isChecked()} == {60, 30}

    dialog.minutes_input.setValue(17)
    assert dialog.total_minutes() == 77
    assert not any(b.isChecked() for b in dialog.chunk_buttons.values())
    assert "Exact duration entered" in dialog.duration_hint.text()
    assert "77 minutes" in dialog.total_label.text()
    dialog.chunk_buttons[5].click()
    assert dialog.total_minutes() == 5  # starts a new sum, no stale 90-minute sum

    dialog.hours_input.setValue(12)
    dialog.minutes_input.setValue(7)
    assert dialog.total_minutes() == 727
    assert not any(b.isChecked() for b in dialog.chunk_buttons.values())
    dialog._validate_and_accept()
    assert dialog.result() == dialog.Accepted
    assert dialog.total_minutes() == 727
    dialog.close()


def test_duration_all_chunk_combinations_keep_their_own_selection():
    from jobtracker.ui.widgets.session_dialog import DurationDialog

    _application()
    dialog = DurationDialog()
    minutes = tuple(dialog.chunk_buttons)
    for mask in range(1 << len(minutes)):
        selected = {m for index, m in enumerate(minutes) if mask & (1 << index)}
        for m, button in dialog.chunk_buttons.items():
            if button.isChecked() != (m in selected):
                button.click()
        assert dialog.total_minutes() == sum(selected)
        assert {m for m, b in dialog.chunk_buttons.items() if b.isChecked()} == selected
    assert dialog.total_minutes() == 480
    assert (dialog.hours_input.value(), dialog.minutes_input.value()) == (8, 0)
    dialog.close()


def test_custom_duration_saves_170_minutes_only_after_add(service, monkeypatch):
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.session_dialog import DurationDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    end = datetime(2026, 6, 21, 0, 30)
    original_add = manager._quick_add
    monkeypatch.setattr(manager, "_quick_add", lambda m: original_add(m, end=end))
    manager._custom_duration()
    QTest.qWait(30)
    qt_app.processEvents()
    dialog = manager.findChild(DurationDialog)
    assert dialog is not None
    for minutes in (120, 30, 15, 5):
        dialog.chunk_buttons[minutes].click()
    assert service.get_sessions_for_subject(subject.id) == []
    dialog.confirm_btn.click()
    QTest.qWait(30)
    qt_app.processEvents()
    sessions = service.get_sessions_for_subject(subject.id)
    assert len(sessions) == 1
    assert sessions[0].duration_seconds == 170 * 60
    assert sessions[0].start_time == "2026-06-20T21:40:00"
    assert sessions[0].end_time == end.isoformat()
    manager.close()


def test_quick_add_90_minute_button_dispatches_exact_duration(service, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    end = datetime(2026, 6, 21, 0, 30)
    original_add = manager._quick_add
    monkeypatch.setattr(manager, "_quick_add", lambda m: original_add(m, end=end))
    button = next(b for b in manager.findChildren(QPushButton) if b.text() == "1h 30m")
    button.click()
    qt_app.processEvents()
    session = service.get_sessions_for_subject(subject.id)[0]
    assert session.duration_seconds == 90 * 60
    assert session.start_time == "2026-06-20T23:00:00"
    manager.close()


@pytest.mark.parametrize("start,end,seconds", [
    (datetime(2026, 3, 28, 1, 30), datetime(2026, 3, 28, 3), 86400),
    (datetime(2026, 3, 28, 2, 30), datetime(2026, 3, 28, 4), 86400),
    (datetime(2026, 3, 30, 1, 30), datetime(2026, 3, 30, 3), -86400),
    (datetime(2026, 10, 24, 2, 30), datetime(2026, 10, 24, 4), 86400),
    (datetime(2026, 10, 26, 2, 30), datetime(2026, 10, 26, 4), -86400),
])
def test_session_editor_day_moves_preserve_wall_clock_at_dst(
    service, monkeypatch, start, end, seconds,
):
    from PySide6.QtWidgets import QPushButton
    from jobtracker.ui.widgets.session_dialog import SessionDialog, apply_session_edits

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(subject.id, start, end)
    with monkeypatch.context() as clock:
        clock.setenv("TZ", "Europe/Amsterdam")
        time.tzset()
        try:
            dialog = SessionDialog(
                None, session, service=service, current_subject_id=subject.id
            )
            label = "+1 day" if seconds > 0 else "−1 day"
            next(b for b in dialog.findChildren(QPushButton) if b.text() == label).click()
            data = dialog.get_data()
            assert data["start_time"] == start + timedelta(seconds=seconds)
            assert data["end_time"] == end + timedelta(seconds=seconds)
            assert data["start_time"].tzinfo is None
            moved = apply_session_edits(service, session, data)
            assert moved.id == session.id and moved.uid == session.uid
            assert moved.duration_seconds == session.duration_seconds
            dialog.close()
        finally:
            clock.undo()
            time.tzset()


def test_day_buttons_dispatch_exact_selected_session_and_leave_original_day(service, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from jobtracker.core import timeutils
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 6, 21, 1, 30)
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))
    other = service.add_session(subject.id, start.replace(hour=0), start.replace(hour=1))
    seen = []
    original_shift = service.shift_session

    def capture(session_id, seconds):
        seen.append((session_id, seconds))
        return original_shift(session_id, seconds)

    monkeypatch.setattr(service, "shift_session", capture)
    manager = ManageSessionsDialog(subject.id, service, select_session_id=session.id)
    manager.show()
    qt_app.processEvents()
    next(b for b in manager.findChildren(QPushButton) if b.text() == "+1 day").click()
    moved = service.get_session(session.id)
    assert seen == [(session.id, 86400)]
    assert moved.start_time == (start + timedelta(days=1)).isoformat()
    assert moved.duration_seconds == session.duration_seconds and moved.uid == session.uid
    assert manager._list.selected_session()["session_id"] == session.id
    assert service.get_session(other.id).start_time == other.start_time
    manager.close()

    day = timeutils.logical_day(start + timedelta(days=1))
    dialog = DaySessionsDialog(service, day, select_session_id=session.id)
    dialog.show()
    qt_app.processEvents()
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "−1 day").click()
    assert seen == [(session.id, 86400), (session.id, -86400)]
    assert dialog._list.count() == 0  # row moves out of this logical-day list
    assert service.get_session(session.id).start_time == session.start_time
    dialog.close()


def test_editor_day_moves_preserve_fractional_endpoints_until_edited(service):
    from jobtracker.ui.widgets.session_dialog import SessionDialog, apply_session_edits

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 3, 28, 9, 0, 0, 900123)
    end = datetime(2026, 3, 28, 10, 30, 0, 100456)
    session = service.add_session(subject.id, start, end)
    dialog = SessionDialog(None, session, service=service, current_subject_id=subject.id)
    assert dialog.get_data()["start_time"] == start
    assert dialog.get_data()["end_time"] == end
    dialog._shift(86400)
    dialog._shift(-900)
    data = dialog.get_data()
    assert data["start_time"] == start + timedelta(days=1, minutes=-15)
    assert data["end_time"] == end + timedelta(days=1, minutes=-15)
    moved = apply_session_edits(service, session, data)
    assert moved.duration_seconds == session.duration_seconds == 5399
    assert moved.id == session.id and moved.uid == session.uid

    # Deliberately changing a datetime uses that field's edited value; it must
    # never resurrect the original fractional endpoint after an edit.
    dialog.start_edit.setDateTime(dialog.start_edit.dateTime().addSecs(60))
    edited = dialog.get_data()
    assert edited["start_time"] == dialog.start_edit.dateTime().toPython()
    assert edited["start_time"] != data["start_time"]
    assert edited["end_time"] == data["end_time"]
    dialog.close()


def test_day_move_does_not_select_another_session_when_target_leaves(service, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from jobtracker.ui.widgets import session_list
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog

    qt_app = _application()
    day, _a, _b, first, second = _day_with_two_sessions(service)
    notices = []
    monkeypatch.setattr(session_list, "information", lambda *_args: notices.append(_args[1]))
    dialog = DaySessionsDialog(service, day, select_session_id=first.id)
    dialog.show()
    qt_app.processEvents()
    button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "+1 day")
    button.click()
    qt_app.processEvents()
    assert dialog._list.count() == 1
    assert dialog._list.selected_session() is None
    moved_start = service.get_session(first.id).start_time
    button.click()
    qt_app.processEvents()
    assert notices == ["No Selection"]
    assert service.get_session(first.id).start_time == moved_start
    assert service.get_session(second.id).start_time == second.start_time
    dialog.close()


@pytest.mark.parametrize("start,seconds", [
    (datetime(100, 1, 1, 9), -86400),
    (datetime(9999, 12, 31, 9), 86400),
])
def test_editor_out_of_range_day_move_keeps_both_fields(service, monkeypatch, start, seconds):
    from jobtracker.ui.widgets import session_dialog

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))
    dialog = session_dialog.SessionDialog(None, session)
    original = dialog.get_data()
    assert original["start_time"] == start
    warnings = []
    monkeypatch.setattr(session_dialog, "warning", lambda *_args: warnings.append(_args[1]))
    dialog._shift(seconds)
    assert warnings == ["Could Not Move"]
    assert dialog.get_data() == original
    assert service.get_session(session.id).start_time == session.start_time
    dialog.close()


@pytest.mark.parametrize("start,seconds", [
    (datetime(1, 1, 1, 9), -86400),
    (datetime(9999, 12, 31, 9), 86400),
])
def test_list_move_outside_datetime_range_leaves_session_intact(service, start, seconds):
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))
    assert service.shift_session(session.id, seconds) is None
    persisted = service.get_session(session.id)
    assert persisted.start_time == session.start_time
    assert persisted.end_time == session.end_time
    assert persisted.duration_seconds == session.duration_seconds and persisted.uid == session.uid


def test_editor_calendar_and_clock_edits_replace_only_changed_endpoint(service, monkeypatch):
    from PySide6.QtCore import QDate, QTime
    from jobtracker.ui.widgets import session_dialog

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 3, 28, 1, 30, 0, 900123)
    end = datetime(2026, 3, 28, 3, 0, 0, 100456)
    session = service.add_session(subject.id, start, end)
    dialog = session_dialog.SessionDialog(None, session)
    assert dialog.start_edit.calendarPopup() and dialog.end_edit.calendarPopup()
    dialog.start_edit.setDate(QDate(2026, 3, 29))
    edited = dialog.get_data()
    assert edited["start_time"].date() == date(2026, 3, 29)
    assert edited["end_time"] == end
    warnings = []
    monkeypatch.setattr(session_dialog, "warning", lambda *_args: warnings.append(_args[1]))
    dialog._validate_and_accept()
    assert warnings == ["Invalid Times"]
    assert dialog.result() == dialog.Rejected

    dialog.end_edit.setDate(QDate(2026, 3, 29))
    dialog.start_edit.setTime(QTime(2, 30, 0, 900))  # DST-skipped local clock is allowed.
    data = dialog.get_data()
    assert data["start_time"] == datetime(2026, 3, 29, 2, 30, 0, 900000)
    assert data["end_time"] == datetime(2026, 3, 29, 3, 0, 0, 100000)
    assert data["start_time"].tzinfo is None
    dialog._shift(86400)
    shifted = dialog.get_data()
    assert shifted["start_time"] == data["start_time"] + timedelta(days=1)
    assert shifted["end_time"] == data["end_time"] + timedelta(days=1)
    dialog._validate_and_accept()
    assert dialog.result() == dialog.Accepted
    moved = session_dialog.apply_session_edits(service, session, shifted)
    assert moved.id == session.id and moved.uid == session.uid
    assert moved.duration_seconds == 1799
    dialog.close()


@pytest.mark.parametrize("seconds", [20, 29, 30])
def test_editor_short_duration_guard_keeps_existing_session(service, monkeypatch, seconds):
    from jobtracker.ui.widgets import session_dialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 6, 20, 9)
    session = service.add_session(subject.id, start, start + timedelta(minutes=90))
    manager = ManageSessionsDialog(subject.id, service, select_session_id=session.id)
    manager.show()
    manager._edit()
    QTest.qWait(30)
    qt_app.processEvents()
    editor = manager.findChild(session_dialog.SessionDialog)
    editor.end_edit.setDateTime(editor.start_edit.dateTime().addSecs(seconds))
    warnings = []
    monkeypatch.setattr(session_dialog, "warning", lambda *_args: warnings.append(_args[1]))
    editor._validate_and_accept()
    QTest.qWait(30)
    qt_app.processEvents()
    persisted = service.get_session(session.id)
    assert persisted is not None and persisted.uid == session.uid
    if seconds < 30:
        assert warnings == ["Duration Too Short"]
        assert persisted.duration_seconds == session.duration_seconds
        assert editor.isVisible()
        editor.reject()
        QTest.qWait(30)
        qt_app.processEvents()
    else:
        assert warnings == []
        assert persisted.duration_seconds == 30
    manager.close()


def test_shared_editor_reports_refused_save(service, monkeypatch):
    from jobtracker.ui.widgets import session_list
    from jobtracker.ui.widgets.session_dialog import SessionDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(subject.id, datetime(2026, 6, 20, 9), datetime(2026, 6, 20, 10))
    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    attempts, warnings = [], []
    monkeypatch.setattr(service, "update_session", lambda *args: attempts.append(args))
    monkeypatch.setattr(session_list, "warning", lambda *_args: warnings.append(_args[1]))
    manager._edit()
    QTest.qWait(30)
    qt_app.processEvents()
    manager.findChild(SessionDialog)._validate_and_accept()
    QTest.qWait(30)
    qt_app.processEvents()
    assert len(attempts) == 1 and attempts[0][0] == session.id
    assert warnings == ["Could Not Save"]
    assert service.get_session(session.id).start_time == session.start_time
    assert manager._list.selected_session()["session_id"] == session.id
    manager.close()


def test_all_manager_creation_paths_report_refused_save(service, monkeypatch):
    from types import SimpleNamespace
    from jobtracker.ui.widgets import manage_sessions_dialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    manager = manage_sessions_dialog.ManageSessionsDialog(subject.id, service)
    manager.show()
    attempts, warnings = [], []
    monkeypatch.setattr(service, "add_session", lambda *args: attempts.append(args))
    monkeypatch.setattr(manage_sessions_dialog, "warning", lambda *_args: warnings.append(_args[1]))
    manager._quick_add(90, end=datetime(2026, 6, 21, 0, 30))
    manager._quick_add_slot(9, 11)
    start = datetime(2026, 6, 20, 9)
    dialog = SimpleNamespace(get_data=lambda: {
        "start_time": start, "end_time": start + timedelta(minutes=170),
    })
    manager._finish_add(manager.Accepted, dialog)
    qt_app.processEvents()
    assert warnings == ["Could Not Add Session"] * 3
    assert [int((end - start).total_seconds()) for _subject, start, end in attempts] == [5400, 7200, 10200]
    assert manager._list.count() == 0
    assert service.get_sessions_for_subject(subject.id) == []
    manager.close()


def test_quick_add_row_has_trimmed_durations_and_custom(service):
    from PySide6.QtWidgets import QPushButton

    from jobtracker.ui.widgets.manage_sessions_dialog import (
        QUICK_DURATIONS,
        ManageSessionsDialog,
    )

    qt_app = _application()
    assert [label for label, _m in QUICK_DURATIONS] == [
        "15m", "30m", "1h", "1h 15m", "1h 30m", "2h",
    ]
    subject = service.add_subject("Physics", "#3B82F6", "")
    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    qt_app.processEvents()
    labels = {b.text() for b in manager.findChildren(QPushButton)}
    for gone in ("45m", "2h 30m"):
        assert gone not in labels
    assert "1h 30m" in labels
    assert "Custom…" in labels

    # The custom duration flows into a real session ending now.
    manager._quick_add(187)
    qt_app.processEvents()
    sessions = service.get_sessions_for_subject(subject.id)
    assert len(sessions) == 1
    assert sessions[0].duration_seconds == 187 * 60
    manager.close()


def test_sessions_list_move_row_shifts_selected_session(service):
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    start = datetime(2026, 6, 20, 14, 0)
    session = service.add_session(subject.id, start, start + timedelta(hours=1))

    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    qt_app.processEvents()

    manager._shift_selected(-900)
    qt_app.processEvents()
    moved = service.get_session(session.id)
    assert datetime.fromisoformat(moved.start_time) == start - timedelta(minutes=15)
    assert moved.duration_seconds == 3600
    # The moved session stays selected so nudges can be chained.
    assert manager._list.selected_session()["session_id"] == session.id
    manager.close()


def test_both_session_lists_share_one_component_and_styling(service):
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.session_list import SessionListView

    qt_app = _application()
    day, subject_a, _b, first, _second = _day_with_two_sessions(service)

    manager = ManageSessionsDialog(subject_a.id, service)
    day_dialog = DaySessionsDialog(service, day)
    manager.show()
    day_dialog.show()
    qt_app.processEvents()

    # Literally the same widget class and the same selection styling.
    assert isinstance(manager._list, SessionListView)
    assert isinstance(day_dialog._list, SessionListView)
    assert "item:selected" in manager._list.styleSheet()
    assert manager._list.styleSheet() == day_dialog._list.styleSheet()

    # Both expose the same selection API returning row dicts.
    assert manager._list.selected_session()["session_id"] == first.id
    assert day_dialog._list.selected_session()["session_id"] == first.id
    manager.close()
    day_dialog.close()


def test_running_session_guard_message_reads_correctly(monkeypatch):
    """The guard is built from a verb, so it must not produce 'editd'."""
    from jobtracker.ui.widgets import session_list

    _application()
    messages: list[str] = []
    monkeypatch.setattr(
        session_list,
        "warning",
        lambda _parent, _title, text, **_kw: messages.append(text),
    )
    live_row = {"session_id": None, "subject_name": "Physics"}
    for action in ("edit", "duplicate", "delete", "move"):
        assert session_list.require_editable(None, live_row, action) is False
    assert [m.split("then you can ")[1] for m in messages] == [
        "edit it here.",
        "duplicate it here.",
        "delete it here.",
        "move it here.",
    ]


def test_enter_edits_the_selection_in_both_session_dialogs(service):
    """Enter must mean the same thing in both lists (it used to close one)."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app = _application()
    day, subject_a, _b, first, _second = _day_with_two_sessions(service)
    enter = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)

    for dialog in (
        ManageSessionsDialog(subject_a.id, service),
        DaySessionsDialog(service, day),
    ):
        dialog.show()
        qt_app.processEvents()
        assert dialog.claims_inline_key(enter) is True
        assert dialog.handle_inline_key(enter) is True
        QTest.qWait(30)
        qt_app.processEvents()
        editor = dialog.findChild(SessionDialog)
        assert editor is not None, f"{type(dialog).__name__} ignored Enter"
        editor.reject()
        qt_app.processEvents()
        # Neither dialog closed itself on Enter.
        assert dialog.isVisible()
        dialog.close()


def test_duplicate_action_is_shared_by_both_dialogs(service):
    """Both dialogs must call one duplicate implementation, not copies."""
    from jobtracker.ui.widgets import day_sessions_dialog, manage_sessions_dialog
    from jobtracker.ui.widgets import session_list

    assert day_sessions_dialog.session_list is session_list
    assert manage_sessions_dialog.session_list is session_list
    assert hasattr(session_list, "duplicate_session")
    # No dialog reimplements the service call itself.
    for module in (day_sessions_dialog, manage_sessions_dialog):
        source = open(module.__file__, encoding="utf-8").read()
        assert "service.duplicate_session(" not in source


def test_quick_add_records_the_exact_duration_across_midnight(service):
    """Quick-add must never silently shorten a session at the day boundary."""
    from jobtracker.core import timeutils
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog

    qt_app = _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    manager = ManageSessionsDialog(subject.id, service)
    manager.show()
    qt_app.processEvents()

    # 2h ending 00:30 -> starts 22:30 the previous calendar day.
    end = datetime(2026, 6, 21, 0, 30)
    manager._quick_add(120, end=end)
    qt_app.processEvents()
    sessions = service.get_sessions_for_subject(subject.id)
    assert len(sessions) == 1
    assert sessions[0].duration_seconds == 2 * 3600
    start = datetime.fromisoformat(sessions[0].start_time)
    assert start == datetime(2026, 6, 20, 22, 30)
    # Both ends belong to one logical day: the day it began.
    assert timeutils.logical_day(start) == timeutils.logical_day(end) == date(2026, 6, 20)

    # A long custom duration is stored in full, not trimmed to today.
    manager._quick_add(12 * 60, end=datetime(2026, 6, 25, 10, 0))
    qt_app.processEvents()
    longest = max(
        service.get_sessions_for_subject(subject.id),
        key=lambda s: s.duration_seconds,
    )
    assert longest.duration_seconds == 12 * 3600
    manager.close()


def test_session_editor_offers_delete_only_when_editing(service):
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )

    adding = SessionDialog(None)
    assert adding.delete_btn is None  # nothing to delete yet
    adding.close()

    editing = SessionDialog(
        None, session, service=service, current_subject_id=subject.id
    )
    assert editing.delete_btn is not None and editing.delete_btn.text() == "✕ Delete"
    editing.close()


def test_declining_the_delete_confirm_keeps_the_session(service, monkeypatch):
    from jobtracker.ui.widgets import session_dialog as session_dialog_module
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    _application()
    subject = service.add_subject("Physics", "#3B82F6", "")
    session = service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )
    dialog = SessionDialog(
        None, session, service=service, current_subject_id=subject.id
    )
    monkeypatch.setattr(
        session_dialog_module,
        "question",
        lambda _p, _t, _x, on_finished, **_kw: on_finished(QMessageBox.No),
    )
    dialog._confirm_delete()
    assert dialog.result() != SessionDialog.DELETE_RESULT
    assert service.get_session(session.id) is not None
    dialog.close()


def test_every_session_list_edit_path_can_delete(service, monkeypatch):
    """Deleting through the shared editor works from both session lists."""
    from jobtracker.ui.widgets import session_dialog as session_dialog_module
    from jobtracker.ui.widgets.day_sessions_dialog import DaySessionsDialog
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.session_dialog import SessionDialog

    qt_app = _application()
    monkeypatch.setattr(
        session_dialog_module,
        "question",
        lambda _p, _t, _x, on_finished, **_kw: on_finished(QMessageBox.Yes),
    )
    day, subject_a, _b, first, second = _day_with_two_sessions(service)

    manager = ManageSessionsDialog(subject_a.id, service)
    manager.show()
    qt_app.processEvents()
    manager._edit()
    QTest.qWait(30)
    qt_app.processEvents()
    manager.findChild(SessionDialog)._confirm_delete()
    QTest.qWait(30)
    qt_app.processEvents()
    assert service.get_session(first.id) is None
    manager.close()

    day_dialog = DaySessionsDialog(service, day)
    day_dialog.show()
    qt_app.processEvents()
    day_dialog._edit_selected()
    QTest.qWait(30)
    qt_app.processEvents()
    day_dialog.findChild(SessionDialog)._confirm_delete()
    QTest.qWait(30)
    qt_app.processEvents()
    assert service.get_session(second.id) is None
    day_dialog.close()


def test_compact_session_presets_keep_exact_session_and_fit_half_screen(database, monkeypatch):
    from jobtracker.ui.widgets.manage_sessions_dialog import ManageSessionsDialog
    from jobtracker.ui.widgets.dialog_utils import open_dialog
    from tests.support.native import _window

    app, window = _window(database, monkeypatch)
    try:
        window.resize(600, 760)
        subject = window.service.add_subject("Subject with a long title " * 5, "#38BDF8", "")
        manager = ManageSessionsDialog(subject.id, window.service, window)
        open_dialog(manager)
        app.processEvents()
        assert manager._quick_panel.isVisible()
        assert manager._move_panel.isVisible()
        assert all(button.text() not in ("Quick Add", "Move selected session") for button in manager.findChildren(QPushButton))
        preset = next(button for button in manager.findChildren(QPushButton) if button.text() == "15m")
        preset.click()
        app.processEvents()
        session = window.service.get_sessions_for_subject(subject.id)[0]
        assert session.duration_seconds == 900
        assert manager._list.selected_session()["session_id"] == session.id
        move = next(button for button in manager._move_panel.findChildren(QPushButton) if button.text() == "+15m")
        start = datetime.fromisoformat(session.start_time)
        move.click()
        app.processEvents()
        moved = window.service.get_session(session.id)
        assert datetime.fromisoformat(moved.start_time) == start + timedelta(minutes=15)
        assert manager._list.selected_session()["session_id"] == session.id
        for panel in (manager._quick_panel, manager._move_panel):
            assert panel.isVisible()
            assert panel.minimumSizeHint().width() <= panel.width()
        assert manager.mapTo(window, manager.rect().bottomRight()).x() < window.width()
        assert manager.mapTo(window, manager.rect().bottomRight()).y() < window.height()
        assert manager._list.height() >= 100
        manager.reject()
    finally:
        window.close()
        app.processEvents()

