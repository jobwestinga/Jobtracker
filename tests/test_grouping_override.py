"""Bucket size: the range picks one, the user may override it.

The default (derive it from the range) is unchanged and still right most of the
time — a year of daily bars is unreadable. What is new is that "show me every
day of last month" is possible, and these check it produces genuinely different
buckets rather than a relabelled version of the same ones.

Also covers the arithmetic that must hold whatever the bucket size: totals are
conserved, buckets are ordered and unique, and no session is dropped or counted
twice.
"""

from datetime import date, datetime, timedelta

import pytest

from jobtracker.core import timeutils
from jobtracker.core.timeutils import GROUPING_CHOICES, resolve_grouping


# ── the resolver ────────────────────────────────────────────────────────


@pytest.mark.parametrize("automatic", ["daily", "weekly", "monthly"])
def test_auto_keeps_whatever_the_range_derived(automatic):
    assert resolve_grouping(automatic, "auto") == automatic
    assert resolve_grouping(automatic, None) == automatic
    assert resolve_grouping(automatic, "") == automatic


@pytest.mark.parametrize("override", ["daily", "weekly", "monthly"])
def test_an_explicit_choice_wins_over_the_range(override):
    for automatic in ("daily", "weekly", "monthly"):
        assert resolve_grouping(automatic, override) == override


@pytest.mark.parametrize("junk", ["hourly", "yearly", "DAILY", "  daily", 7, None, True])
def test_a_meaningless_override_falls_back_to_automatic(junk):
    """A stale or corrupted setting must not produce an empty chart."""
    assert resolve_grouping("weekly", junk) == "weekly"


def test_the_choices_are_what_the_ui_offers():
    assert GROUPING_CHOICES == ("auto", "daily", "weekly", "monthly")


def test_the_range_defaults_are_unchanged():
    """The presets people already rely on must keep behaving the same."""
    assert timeutils.grouping_for_preset("weeks") == "daily"
    assert timeutils.grouping_for_preset("months") == "weekly"
    assert timeutils.grouping_for_preset("year") == "monthly"
    assert timeutils.grouping_for_preset("all") == "monthly"


# ── real breakdowns at each bucket size ─────────────────────────────────


@pytest.fixture
def two_months(service):
    """Sixty consecutive days of one-hour sessions, on two subjects."""
    physics = service.add_subject("Physics", "#3B82F6", "")
    maths = service.add_subject("Maths", "#EF4444", "")
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    for offset in range(60):
        day = base - timedelta(days=offset)
        subject = physics if offset % 2 == 0 else maths
        service.add_session(subject.id, day, day + timedelta(hours=1))
    return service


def window(service):
    """The exact 60-day span the fixture filled, as explicit dates.

    Used instead of ``days=60`` wherever a test needs the SAME window at every
    bucket size: ``days=N`` deliberately snaps to whole weeks/months (see
    ``_resolve_logical_window``), so the windows would not be comparable.
    """
    today = timeutils.logical_day(datetime.now())
    return today - timedelta(days=59), today


def totals_of(buckets):
    return sum(b["total_seconds"] for b in buckets)


def test_monthly_range_can_show_every_day(two_months):
    """The headline request: a month, one bar per day rather than per week."""
    start, end = window(two_months)
    start = end - timedelta(days=29)
    weekly = two_months.get_subject_breakdown(
        grouping="weekly", days=None, start_date=start, end_date=end
    )
    daily = two_months.get_subject_breakdown(
        grouping="daily", days=None, start_date=start, end_date=end
    )

    assert len(daily) > len(weekly)
    assert len(daily) == 30
    # Same work, just cut differently — nothing gained or lost by the choice.
    assert totals_of(daily) == totals_of(weekly) == 30 * 3600


def test_yearly_range_can_show_weeks(two_months):
    start, end = window(two_months)
    monthly = two_months.get_subject_breakdown(
        grouping="monthly", days=None, start_date=start, end_date=end
    )
    weekly = two_months.get_subject_breakdown(
        grouping="weekly", days=None, start_date=start, end_date=end
    )

    assert len(weekly) > len(monthly)
    assert totals_of(weekly) == totals_of(monthly) == 60 * 3600


@pytest.mark.parametrize("grouping", ["daily", "weekly", "monthly"])
def test_every_bucket_size_conserves_the_total(two_months, grouping):
    start, end = window(two_months)
    buckets = two_months.get_subject_breakdown(
        grouping=grouping, days=None, start_date=start, end_date=end
    )
    assert totals_of(buckets) == 60 * 3600


@pytest.mark.parametrize("grouping", ["daily", "weekly", "monthly"])
def test_buckets_are_ordered_and_unique(two_months, grouping):
    start, end = window(two_months)
    buckets = two_months.get_subject_breakdown(
        grouping=grouping, days=None, start_date=start, end_date=end
    )
    keys = [b["date"] for b in buckets]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("grouping", ["daily", "weekly", "monthly"])
def test_a_buckets_total_equals_its_segments(two_months, grouping):
    """A bar's height must equal the pieces stacked inside it."""
    start, end = window(two_months)
    for bucket in two_months.get_subject_breakdown(
        grouping=grouping, days=None, start_date=start, end_date=end
    ):
        assert bucket["total_seconds"] == sum(s["seconds"] for s in bucket["segments"])


@pytest.mark.parametrize("grouping", ["daily", "weekly", "monthly"])
def test_no_session_is_lost_or_double_counted(two_months, grouping):
    start, end = window(two_months)
    buckets = two_months.get_subject_breakdown(
        grouping=grouping, days=None, start_date=start, end_date=end
    )
    segments = [s for b in buckets for s in b["segments"]]
    assert len(segments) == 60


def test_weekly_buckets_start_on_monday(two_months):
    start, end = window(two_months)
    for bucket in two_months.get_subject_breakdown(
        grouping="weekly", days=None, start_date=start, end_date=end
    ):
        assert timeutils.parse_iso(f"{bucket['date']}T00:00:00").weekday() == 0


def test_monthly_buckets_start_on_the_first(two_months):
    start, end = window(two_months)
    for bucket in two_months.get_subject_breakdown(
        grouping="monthly", days=None, start_date=start, end_date=end
    ):
        assert bucket["date"].endswith("-01")


def test_daily_buckets_are_one_per_calendar_day(two_months):
    buckets = two_months.get_subject_breakdown(grouping="daily", days=14)
    days = [timeutils.parse_iso(f"{b['date']}T00:00:00").date() for b in buckets]
    gaps = {(b - a).days for a, b in zip(days, days[1:])}
    assert gaps <= {1}


def test_switching_bucket_size_never_changes_the_subject_split(two_months):
    """Physics and Maths each did half the work; that is true at any zoom."""
    for grouping in ("daily", "weekly", "monthly"):
        per_subject = {}
        start, end = window(two_months)
        for bucket in two_months.get_subject_breakdown(
            grouping=grouping, days=None, start_date=start, end_date=end
        ):
            for segment in bucket["segments"]:
                per_subject[segment["subject_name"]] = (
                    per_subject.get(segment["subject_name"], 0) + segment["seconds"]
                )
        assert per_subject == {"Physics": 30 * 3600, "Maths": 30 * 3600}, grouping


def test_an_empty_database_gives_empty_buckets_not_an_error(service):
    for grouping in ("daily", "weekly", "monthly"):
        buckets = service.get_subject_breakdown(grouping=grouping, days=30)
        assert all(b["total_seconds"] == 0 for b in buckets)


# ── the whole-bucket window rule, pinned ────────────────────────────────


def test_days_with_weekly_grouping_snaps_to_whole_weeks(two_months):
    """`days=N` is deliberately read as "N days' worth of whole buckets".

    A rolling 60-day window cut into weeks would start and end mid-week and draw
    two stubby half-height bars. Snapping avoids that, at the cost of the window
    not being literally 60 days — which is why comparisons across bucket sizes
    in this file use explicit dates instead.
    """
    buckets = two_months.get_subject_breakdown(grouping="weekly", days=60)
    assert len(buckets) == 9                       # ceil(60 / 7)
    for bucket in buckets:
        assert date.fromisoformat(bucket["date"]).weekday() == 0


def test_days_with_monthly_grouping_snaps_to_whole_months(two_months):
    buckets = two_months.get_subject_breakdown(grouping="monthly", days=60)
    assert len(buckets) == 2                       # ceil(60 / 30)
    for bucket in buckets:
        assert bucket["date"].endswith("-01")


def test_days_with_daily_grouping_is_exactly_that_many_days(two_months):
    assert len(two_months.get_subject_breakdown(grouping="daily", days=60)) == 60
    assert len(two_months.get_subject_breakdown(grouping="daily", days=14)) == 14


def test_snapping_never_invents_time_that_was_not_worked(two_months):
    """Whatever the window does at its edges, no bar may exceed reality."""
    for grouping in ("daily", "weekly", "monthly"):
        buckets = two_months.get_subject_breakdown(grouping=grouping, days=60)
        assert totals_of(buckets) <= 60 * 3600, grouping
