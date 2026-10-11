"""Offscreen native entry tests against isolated, real local service writes."""

from PySide6.QtCore import QDate, QPointF, Qt
from PySide6.QtWidgets import QApplication
import pytest

from jobtracker.core import weight_plot
from jobtracker.services.weight_service import WeightService
from jobtracker.ui.widgets import weight_view as module


@pytest.fixture(autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def view(service, application):
    widget = module.WeightView(service)
    widget.resize(620, 900)
    widget.show()
    application.processEvents()
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        application.processEvents()


def day():
    return QDate.currentDate().toString(Qt.ISODate)


def tile(widget, value):
    return next(button for button in widget._whole_buttons if button.text() == str(value))


def test_save_requires_both_picker_rows_and_uses_calendar_day(view, service):
    assert not view._save_button.isEnabled()
    tile(view, 80).click()
    assert not view._save_button.isEnabled()
    view._decimal_buttons[0].click()
    assert view._save_button.isEnabled()
    assert view._save_button.y() > view._decimal_buttons[0].y()
    view._save_button.click()
    rows = service.list_weight_measurements()
    assert len(rows) == 1 and rows[0]["weight_kg"] == 80
    assert rows[0]["measured_at"] == day() + "T00:00:00"
    assert view._status.text() == "Saved."
    assert view._history.topLevelItem(0).text(0) == day()


def test_whole_and_decimal_selection_survive_reopening(view, service):
    tile(view, 81).click()
    view._decimal_buttons[7].click()
    other = module.WeightView(service)
    try:
        assert other._whole == 81 and other._decimal == 7
        assert other._save_button.isEnabled()
        assert other._preview.text() == "81.7 kg"
    finally:
        other.deleteLater()


def test_seven_tiles_center_on_latest_using_phone_rounding(service):
    service.upsert_weight(day(), 82.5)
    widget = module.WeightView(service)
    try:
        assert [button.text() for button in widget._whole_buttons] == [str(value) for value in range(80, 87)]
        widget._select_whole(83)
        widget._select_decimal(6)
        widget._save()
        assert [button.text() for button in widget._whole_buttons] == [str(value) for value in range(81, 88)]
    finally:
        widget.deleteLater()


def test_shift_requires_a_visible_whole_selection(view):
    tile(view, 80).click(); view._decimal_buttons[4].click()
    view._shift_range(7)
    assert not any(button.isChecked() for button in view._whole_buttons)
    assert view._decimal == 4
    assert not view._save_button.isEnabled()
    assert view._whole is None and view._exact_value is None


def test_date_switch_updates_that_day_without_moving_other_reading(view, service):
    service.upsert_weight(day(), 80)
    yesterday = QDate.currentDate().addDays(-1)
    service.upsert_weight(yesterday.toString(Qt.ISODate), 81)
    view.refresh()
    view._today()
    first_uid = view._editing_uid
    view._date.setDate(yesterday)
    assert view._editing_uid != first_uid
    view._select_decimal(9); view._save()
    rows = service.list_weight_measurements()
    assert len(rows) == 2
    assert rows[-1]["uid"] == first_uid and rows[-1]["weight_kg"] == 80
    assert rows[0]["weight_kg"] == 81.9


def test_imported_scale_edit_preserves_timestamp_source_and_exact_precision(view, service):
    WeightService(service.db).import_rows([{
        "measured_at": day() + "T09:17:33", "weight_kg": 83.96, "source": "scale",
    }])
    view.refresh()
    original = service.list_weight_measurements()[0]
    view.edit_entry(original["uid"])
    assert view._preview.text() == "83.96 kg"
    assert view._whole == 84 and view._decimal == 0
    view._save()
    saved = service.list_weight_measurements()[0]
    assert saved["weight_kg"] == 83.96
    assert saved["measured_at"] == original["measured_at"] and saved["source"] == "scale"
    view._select_whole(83); view._select_decimal(9); view._save()
    assert service.list_weight_measurements()[0]["weight_kg"] == 83.9


def test_old_date_backfill_and_today_keep_latest_chronological_anchor(view, service):
    service.upsert_weight(day(), 83.6)
    view.refresh()
    view._date.setDate(QDate.currentDate().addDays(-20))
    view._precise_changed(60.4)
    view._save()
    assert view._center == 84
    assert not view._save_button.isEnabled()
    assert service.list_weight_measurements()[-1]["weight_kg"] == 83.6
    view._today()
    assert view._center == 84 and view._value() == 83.6
    assert view._save_button.isEnabled()


def test_canonical_uid_repair_keeps_draft_and_updates_repaired_daily_row(view, service):
    original = service.upsert_weight(day(), 82.8)["measurement"]
    view.refresh(); view._today()
    service.delete_weight(original["uid"])
    canonical = service.upsert_weight(day(), 82.8)["measurement"]
    view._select_decimal(9)
    view.refresh()
    assert view._editing_uid == canonical["uid"] and view._value() == 82.9
    view._save()
    assert service.list_weight_measurements()[0]["weight_kg"] == 82.9
    assert view._status.text() == "Saved."


def test_removed_edit_is_explained_without_resurrecting_measurement(view, service):
    original = service.upsert_weight(day(), 80)["measurement"]
    view.refresh(); view._today()
    service.delete_weight(original["uid"])
    view.refresh(); view._select_decimal(4); view._save()
    assert service.list_weight_measurements() == []
    assert "removed" in view._status.text()
    view._today(); view._save()
    assert service.list_weight_measurements()[0]["weight_kg"] == 80.4


def test_refresh_preserves_unsaved_selection_and_does_not_refit_unchanged_chart(view, service, monkeypatch):
    service.upsert_weight(day(), 80)
    view.refresh(); view._today()
    calls = []
    original = weight_plot.trend_for
    monkeypatch.setattr(weight_plot, "trend_for", lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs))
    view._select_decimal(7)
    view.refresh()
    view.resize(700, 900)
    QApplication.instance().processEvents()
    assert calls == [] and view._value() == 80.7
    service.upsert_weight(day(), 81)
    view.refresh()
    assert calls == [1] and view._value() == 80.7


def test_options_are_local_persistent_and_chart_handles_historic_only(view, service):
    view._density.setCurrentIndex(view._density.findData("compact"))
    view._contrast.setChecked(True)
    view._range.setCurrentIndex(view._range.findData("all"))
    view._smoothing.setCurrentIndex(view._smoothing.findData("mean7"))
    assert service.get_setting("weight_ui_density") == "compact"
    assert service.get_setting("weight_ui_contrast") == "1"
    assert service.get_setting("weight_chart_range") == "all"
    WeightService(service.db).import_rows([{"measured_at": "2010-01-01", "weight_kg": 60, "source": "historic"}])
    view.refresh()
    assert view._chart._points == []
    view._historic.setChecked(True)
    assert len(view._chart._points) == 1
    assert view._span.isEnabled() is False
    view._smoothing.setCurrentIndex(view._smoothing.findData("loess"))
    QApplication.instance().processEvents()
    assert len(view._chart._trend) == 1


def test_duplicate_measurements_have_safe_hover_and_click(view, service):
    WeightService(service.db).import_rows([
        {"measured_at": day(), "weight_kg": 80, "source": "manual"},
        {"measured_at": day(), "weight_kg": 80, "source": "scale"},
    ])
    view.refresh()
    view._chart.repaint()
    QApplication.instance().processEvents()
    assert len(view._chart._positions) == 2
    position = view._chart._positions[0][0]
    assert view._chart._near(QPointF(position)) is not None


def test_failed_save_keeps_draft_for_retry(view, service, monkeypatch):
    tile(view, 80).click(); view._decimal_buttons[3].click()
    monkeypatch.setattr(service, "upsert_weight", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Synthetic disk failure")))
    view._save()
    assert "Could not save" in view._status.text()
    assert view._value() == 80.3 and view._save_button.isEnabled()
    assert service.list_weight_measurements() == []


def test_history_search_edit_and_delete_use_stable_uid(view, service):
    measurement = service.upsert_weight(day(), 80)["measurement"]
    view.refresh()
    view._search.setText("historic")
    assert view._history.topLevelItem(0).isHidden()
    view._search.clear()
    view._history.setCurrentItem(view._history.topLevelItem(0))
    assert view._edit_button.isEnabled() and view._delete_button.isEnabled()
    view._edit_button.click()
    assert view._editing_uid == measurement["uid"]
    view._delete_entry(measurement["uid"])
    assert service.list_weight_measurements() == []
    assert view._editing_uid is None


def test_calendar_midnight_advances_automatic_date_but_keeps_unsaved_draft(view, service, monkeypatch):
    current = QDate.currentDate()
    service.upsert_weight(day(), 80)
    view.refresh(); view._today()
    monkeypatch.setattr(module, "_current_date", lambda: current.addDays(1))
    view._check_day()
    assert view._date.date() == current.addDays(1)
    assert view._editing_uid is None
    view._select_decimal(7)
    monkeypatch.setattr(module, "_current_date", lambda: current.addDays(2))
    view._check_day()
    assert view._date.date() == current.addDays(1)
    assert view._value() == 80.7


def test_calendar_midnight_keeps_explicit_historical_edit(view, service, monkeypatch):
    current = QDate.currentDate()
    row = service.upsert_weight(day(), 80)["measurement"]
    view.refresh(); view.edit_entry(row["uid"])
    monkeypatch.setattr(module, "_current_date", lambda: current.addDays(1))
    view._check_day()
    assert view._date.date() == current
    assert view._editing_uid == row["uid"]


def test_saved_draft_preserved_over_midnight_then_advances_to_today(view, service, monkeypatch):
    original_day = QDate.currentDate()
    view._select_whole(80); view._select_decimal(7)
    next_day = original_day.addDays(1)
    monkeypatch.setattr(module, "_current_date", lambda: next_day)
    view._check_day()
    assert view._date.date() == original_day
    view._save()
    assert service.list_weight_measurements()[0]["measured_at"][:10] == original_day.toString(Qt.ISODate)
    assert view._date.date() == next_day and view._editing_uid is None
    view._save()
    assert len(service.list_weight_measurements()) == 2
