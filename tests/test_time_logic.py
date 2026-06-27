"""Pure unit tests for the centralized time / logical-day layer."""

from datetime import date, datetime, time

from jobtracker.core import timeutils


# ── Parsing / durations ──────────────────────────────────────────────────────
def test_parse_iso_roundtrip():
    moment = datetime(2026, 6, 20, 14, 30, 5)
    assert timeutils.parse_iso(timeutils.to_iso(moment)) == moment


def test_parse_iso_invalid_returns_none():
    assert timeutils.parse_iso("not-a-date") is None
    assert timeutils.parse_iso("") is None
    assert timeutils.parse_iso(None) is None


def test_duration_seconds_basic():
    start = datetime(2026, 6, 20, 10, 0, 0)
    end = datetime(2026, 6, 20, 11, 30, 0)
    assert timeutils.duration_seconds(start, end) == 5400


def test_duration_seconds_negative_is_clamped():
    start = datetime(2026, 6, 20, 12, 0, 0)
    end = datetime(2026, 6, 20, 11, 0, 0)
    assert timeutils.duration_seconds(start, end) == 0


def test_duration_seconds_missing_returns_zero():
    assert timeutils.duration_seconds(None, datetime.now()) == 0
    assert timeutils.duration_seconds(datetime.now(), None) == 0


# ── day_start parsing ────────────────────────────────────────────────────────
def test_parse_day_start_valid():
    assert timeutils.parse_day_start("03:00") == time(3, 0)
    assert timeutils.parse_day_start("3:00") == time(3, 0)
    assert timeutils.parse_day_start("23:30") == time(23, 30)


def test_parse_day_start_invalid_falls_back_to_default():
    assert timeutils.parse_day_start("garbage") == timeutils.DEFAULT_DAY_START
    assert timeutils.parse_day_start("") == timeutils.DEFAULT_DAY_START
    assert timeutils.parse_day_start(None) == timeutils.DEFAULT_DAY_START


def test_day_start_to_str():
    assert timeutils.day_start_to_str(time(3, 0)) == "03:00"


def test_default_day_start_is_three_am():
    assert timeutils.DEFAULT_DAY_START == time(3, 0)


# ── Logical day with 03:00 boundary ──────────────────────────────────────────
DAY_START = time(3, 0)


def test_before_3am_belongs_to_previous_logical_day():
    moment = datetime(2026, 6, 20, 2, 30)
    assert timeutils.logical_day(moment, DAY_START) == date(2026, 6, 19)


def test_after_3am_belongs_to_same_logical_day():
    moment = datetime(2026, 6, 20, 4, 0)
    assert timeutils.logical_day(moment, DAY_START) == date(2026, 6, 20)


def test_exactly_3am_belongs_to_new_logical_day():
    moment = datetime(2026, 6, 20, 3, 0, 0)
    assert timeutils.logical_day(moment, DAY_START) == date(2026, 6, 20)


def test_evening_belongs_to_same_logical_day():
    moment = datetime(2026, 6, 20, 23, 0)
    assert timeutils.logical_day(moment, DAY_START) == date(2026, 6, 20)


def test_midnight_default_boundary_matches_calendar():
    # With a midnight boundary, logical day == calendar date.
    moment = datetime(2026, 6, 20, 0, 30)
    assert timeutils.logical_day(moment, time(0, 0)) == date(2026, 6, 20)


def test_session_23_to_02_is_one_logical_day():
    # A 23:00 -> 02:00 session stays in a single logical day (the start day).
    start = datetime(2026, 6, 20, 23, 0)
    end = datetime(2026, 6, 21, 2, 0)
    assert timeutils.logical_day(start, DAY_START) == date(2026, 6, 20)
    assert timeutils.logical_day(end, DAY_START) == date(2026, 6, 20)


# ── Bounds + split ───────────────────────────────────────────────────────────
def test_logical_day_bounds():
    start, end = timeutils.logical_day_bounds(date(2026, 6, 20), DAY_START)
    assert start == datetime(2026, 6, 20, 3, 0)
    assert end == datetime(2026, 6, 21, 3, 0)


def test_split_session_within_one_logical_day():
    start = datetime(2026, 6, 20, 23, 0)
    end = datetime(2026, 6, 21, 2, 0)
    chunks = timeutils.split_by_logical_day(start, end, DAY_START)
    assert chunks == [(date(2026, 6, 20), 3 * 3600)]


def test_split_session_crossing_3am_boundary():
    # 02:00 -> 04:00 straddles the 03:00 boundary -> two logical days.
    start = datetime(2026, 6, 20, 2, 0)
    end = datetime(2026, 6, 20, 4, 0)
    chunks = timeutils.split_by_logical_day(start, end, DAY_START)
    assert chunks == [
        (date(2026, 6, 19), 3600),  # 02:00 -> 03:00 belongs to previous day
        (date(2026, 6, 20), 3600),  # 03:00 -> 04:00 belongs to new day
    ]


def test_split_zero_length_returns_empty():
    moment = datetime(2026, 6, 20, 10, 0)
    assert timeutils.split_by_logical_day(moment, moment, DAY_START) == []


# ── week / month / bucket helpers ────────────────────────────────────────────
def test_week_start_is_monday():
    # 2026-06-18 is a Thursday; its week starts Monday 2026-06-15.
    assert timeutils.week_start(date(2026, 6, 18)) == date(2026, 6, 15)
    assert timeutils.week_start(date(2026, 6, 15)) == date(2026, 6, 15)


def test_month_start():
    assert timeutils.month_start(date(2026, 6, 18)) == date(2026, 6, 1)


def test_bucket_key_daily_weekly_monthly():
    day = date(2026, 6, 18)
    assert timeutils.bucket_key(day, "daily") == day
    assert timeutils.bucket_key(day, "weekly") == date(2026, 6, 15)
    assert timeutils.bucket_key(day, "monthly") == date(2026, 6, 1)


def test_logical_day_range_inclusive():
    rng = timeutils.logical_day_range(date(2026, 6, 1), date(2026, 6, 3))
    assert rng == [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
