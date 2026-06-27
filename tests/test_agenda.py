"""Agenda timeline placement: logical-day grouping + late-night at bottom."""

from datetime import date, datetime, time

from jobtracker.core import timeutils

DAY_START = time(3, 0)


# ── agenda_hour helper ───────────────────────────────────────────────────────
def test_agenda_hour_daytime():
    assert timeutils.agenda_hour(datetime(2026, 6, 20, 6, 30), DAY_START) == 6.5
    assert timeutils.agenda_hour(datetime(2026, 6, 20, 23, 0), DAY_START) == 23.0


def test_agenda_hour_after_midnight_is_pushed_past_24():
    assert timeutils.agenda_hour(datetime(2026, 6, 21, 0, 30), DAY_START) == 24.5
    assert timeutils.agenda_hour(datetime(2026, 6, 21, 2, 0), DAY_START) == 26.0


def test_agenda_hour_label():
    assert timeutils.agenda_hour_label(6) == "06:00"
    assert timeutils.agenda_hour_label(23) == "23:00"
    assert timeutils.agenda_hour_label(24) == "00:00 (+1)"
    assert timeutils.agenda_hour_label(25) == "01:00 (+1)"


# ── service.get_agenda_data ──────────────────────────────────────────────────
def test_daytime_session_normal_hours(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 11, 0))
    day_keys, sessions = service.get_agenda_data(date(2026, 6, 20), date(2026, 6, 20), DAY_START)
    assert "2026-06-20" in day_keys
    assert len(sessions) == 1
    s = sessions[0]
    assert s["day"] == "2026-06-20"
    assert s["start_h"] == 9.0
    assert s["end_h"] == 11.0


def test_after_midnight_session_renders_at_bottom_of_previous_day(service, subject):
    # 01:00-02:00 on the 21st belongs to logical day the 20th, drawn at 25..26.
    service.add_session(subject.id, datetime(2026, 6, 21, 1, 0), datetime(2026, 6, 21, 2, 0))
    _keys, sessions = service.get_agenda_data(date(2026, 6, 20), date(2026, 6, 21), DAY_START)
    late = [s for s in sessions if s["start_h"] >= 24]
    assert len(late) == 1
    assert late[0]["day"] == "2026-06-20"
    assert late[0]["start_h"] == 25.0
    assert late[0]["end_h"] == 26.0


def test_session_crossing_midnight_within_logical_day(service, subject):
    # 23:00 -> 00:30 next day: same logical day (20th), spans 23 -> 24.5.
    service.add_session(subject.id, datetime(2026, 6, 20, 23, 0), datetime(2026, 6, 21, 0, 30))
    _keys, sessions = service.get_agenda_data(date(2026, 6, 20), date(2026, 6, 20), DAY_START)
    assert len(sessions) == 1
    assert sessions[0]["start_h"] == 23.0
    assert sessions[0]["end_h"] == 24.5


def test_session_spilling_into_next_logical_day_is_clamped(service, subject):
    # 23:00 -> 04:00 next day crosses the 03:00 boundary into the next logical
    # day; the previous day's bar is clamped to the day edge (24 + 3 = 27).
    service.add_session(subject.id, datetime(2026, 6, 20, 23, 0), datetime(2026, 6, 21, 4, 0))
    _keys, sessions = service.get_agenda_data(date(2026, 6, 20), date(2026, 6, 21), DAY_START)
    twentieth = [s for s in sessions if s["day"] == "2026-06-20"]
    assert len(twentieth) == 1
    assert twentieth[0]["start_h"] == 23.0
    assert twentieth[0]["end_h"] == 27.0


def test_normal_morning_day_stays_compact(service, subject):
    # A 06:00 start should not produce any agenda hours below 6 -> no empty top.
    service.add_session(subject.id, datetime(2026, 6, 20, 6, 0), datetime(2026, 6, 20, 9, 0))
    _keys, sessions = service.get_agenda_data(date(2026, 6, 20), date(2026, 6, 20), DAY_START)
    assert min(s["start_h"] for s in sessions) >= 6.0
