"""Daily / weekly / monthly grouping + custom range, all logical-day aware."""

from datetime import date, datetime, time, timedelta

from jobtracker.core import timeutils
from jobtracker.ui.widgets.graphs_view import _intensity_style

DAY_START = time(3, 0)


def _totals(breakdown):
    return {d["date"]: d["total_seconds"] for d in breakdown}


# ── daily ────────────────────────────────────────────────────────────────────
def test_daily_grouping_before_and_after_3am(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 2, 0), datetime(2026, 6, 20, 2, 30))
    service.add_session(subject.id, datetime(2026, 6, 20, 4, 0), datetime(2026, 6, 20, 5, 0))
    totals = _totals(service.get_subject_breakdown(grouping="daily", days=None, day_start=DAY_START))
    assert totals.get("2026-06-19", 0) == 1800
    assert totals.get("2026-06-20", 0) == 3600


def test_cross_midnight_session_counts_on_start_day(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 23, 0), datetime(2026, 6, 21, 2, 0))
    totals = _totals(service.get_subject_breakdown(grouping="daily", days=None, day_start=DAY_START))
    assert totals.get("2026-06-20", 0) == 3 * 3600
    assert totals.get("2026-06-21", 0) == 0


# ── weekly ───────────────────────────────────────────────────────────────────
def test_weekly_grouping_sums_same_week(service, subject):
    # Two days in the same ISO (Monday-start) week.
    d1 = date(2026, 6, 16)  # Tue
    d2 = date(2026, 6, 18)  # Thu
    assert timeutils.week_start(d1) == timeutils.week_start(d2)
    service.add_session(subject.id, datetime(d1.year, d1.month, d1.day, 10), datetime(d1.year, d1.month, d1.day, 11))
    service.add_session(subject.id, datetime(d2.year, d2.month, d2.day, 10), datetime(d2.year, d2.month, d2.day, 11))

    breakdown = service.get_subject_breakdown(grouping="weekly", days=None, day_start=DAY_START)
    totals = _totals(breakdown)
    week_key = timeutils.week_start(d1).isoformat()
    assert totals.get(week_key, 0) == 2 * 3600


def test_weekly_grouping_separates_different_weeks(service, subject):
    d1 = date(2026, 6, 16)   # week A
    d2 = date(2026, 6, 24)   # week B
    assert timeutils.week_start(d1) != timeutils.week_start(d2)
    service.add_session(subject.id, datetime(d1.year, d1.month, d1.day, 10), datetime(d1.year, d1.month, d1.day, 11))
    service.add_session(subject.id, datetime(d2.year, d2.month, d2.day, 10), datetime(d2.year, d2.month, d2.day, 11))

    totals = _totals(service.get_subject_breakdown(grouping="weekly", days=None, day_start=DAY_START))
    assert totals.get(timeutils.week_start(d1).isoformat(), 0) == 3600
    assert totals.get(timeutils.week_start(d2).isoformat(), 0) == 3600


# ── monthly ──────────────────────────────────────────────────────────────────
# NOTE: explicit start_date/end_date is used here so the window does not depend
# on the real "today" (all-time / last-N windows end at the current date).
def test_monthly_grouping(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 10, 10), datetime(2026, 6, 10, 11))
    service.add_session(subject.id, datetime(2026, 6, 20, 10), datetime(2026, 6, 20, 11))
    service.add_session(subject.id, datetime(2026, 7, 5, 10), datetime(2026, 7, 5, 12))

    totals = _totals(service.get_subject_breakdown(
        grouping="monthly", day_start=DAY_START,
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 31),
    ))
    assert totals.get("2026-06-01", 0) == 2 * 3600
    assert totals.get("2026-07-01", 0) == 2 * 3600


def test_monthly_late_night_first_of_month_counts_previous_month(service, subject):
    # 2026-07-01 01:00 (before 03:00) -> logical day 2026-06-30 -> June bucket.
    service.add_session(subject.id, datetime(2026, 7, 1, 1, 0), datetime(2026, 7, 1, 2, 0))
    totals = _totals(service.get_subject_breakdown(
        grouping="monthly", day_start=DAY_START,
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 31),
    ))
    assert totals.get("2026-06-01", 0) == 3600
    assert totals.get("2026-07-01", 0) == 0


# ── custom range ─────────────────────────────────────────────────────────────
def test_custom_range_includes_only_in_range(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 10, 10), datetime(2026, 6, 10, 11))  # in
    service.add_session(subject.id, datetime(2026, 6, 20, 10), datetime(2026, 6, 20, 11))  # in
    service.add_session(subject.id, datetime(2026, 5, 1, 10), datetime(2026, 5, 1, 11))    # out

    breakdown = service.get_subject_breakdown(
        grouping="daily", day_start=DAY_START,
        start_date=date(2026, 6, 9), end_date=date(2026, 6, 21),
    )
    totals = _totals(breakdown)
    assert totals.get("2026-06-10", 0) == 3600
    assert totals.get("2026-06-20", 0) == 3600
    assert "2026-05-01" not in totals


def test_custom_range_reversed_dates_are_normalized(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 10, 10), datetime(2026, 6, 10, 11))
    breakdown = service.get_subject_breakdown(
        grouping="daily", day_start=DAY_START,
        start_date=date(2026, 6, 21), end_date=date(2026, 6, 9),  # reversed
    )
    assert _totals(breakdown).get("2026-06-10", 0) == 3600


# ── live session inclusion ───────────────────────────────────────────────────
def test_live_session_in_weekly_grouping(service, subject):
    service.start_subject(subject.id)
    past = (datetime.now() - timedelta(minutes=10)).isoformat()
    service.db.connection.execute(
        "UPDATE sessions SET start_time = ? WHERE id = ?", (past, service.active_session.id)
    )
    service.db.connection.commit()
    service.active_session.start_time = past

    day_start = service.get_day_start()
    logical = timeutils.logical_day(datetime.fromisoformat(past), day_start)
    week_key = timeutils.week_start(logical).isoformat()
    totals = _totals(service.get_subject_breakdown(grouping="weekly", days=None))
    assert totals.get(week_key, 0) >= 540


def test_weekly_presets_align_to_one_and_two_week_buckets(service, subject):
    today = timeutils.logical_day(datetime.now(), DAY_START)
    this_monday = timeutils.week_start(today)
    previous_monday = this_monday - timedelta(days=7)
    service.add_session(
        subject.id,
        datetime.combine(this_monday, time(10)),
        datetime.combine(this_monday, time(11)),
    )
    service.add_session(
        subject.id,
        datetime.combine(previous_monday, time(10)),
        datetime.combine(previous_monday, time(11)),
    )

    seven = service.get_subject_breakdown(
        grouping="weekly", days=7, day_start=DAY_START
    )
    fourteen = service.get_subject_breakdown(
        grouping="weekly", days=14, day_start=DAY_START
    )
    assert [row["date"] for row in seven] == [this_monday.isoformat()]
    assert [row["date"] for row in fourteen] == [
        previous_monday.isoformat(), this_monday.isoformat(),
    ]


def test_monthly_preset_aligns_to_current_month(service, subject):
    today = timeutils.logical_day(datetime.now(), DAY_START)
    month = timeutils.month_start(today)
    service.add_session(
        subject.id,
        datetime.combine(month, time(10)),
        datetime.combine(month, time(11)),
    )
    rows = service.get_subject_breakdown(
        grouping="monthly", days=30, day_start=DAY_START
    )
    assert [row["date"] for row in rows] == [month.isoformat()]


def test_weekly_and_monthly_intensity_is_daily_average(service, subject):
    week_start = date(2026, 6, 15)
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        service.add_session(
            subject.id,
            datetime.combine(day, time(4)),
            datetime.combine(day, time(14)),
        )
    weekly = service.get_subject_breakdown(
        grouping="weekly",
        day_start=DAY_START,
        start_date=week_start,
        end_date=week_start + timedelta(days=6),
    )[0]
    assert weekly["total_seconds"] == 70 * 3600
    assert weekly["period_days"] == 7
    assert weekly["intensity_seconds"] == 10 * 3600
    assert _intensity_style(weekly["intensity_seconds"])[0] == "#FB923C"

    monthly = service.get_subject_breakdown(
        grouping="monthly",
        day_start=DAY_START,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
    )[0]
    assert monthly["period_days"] == 30
    assert monthly["intensity_seconds"] == monthly["total_seconds"] / 30
