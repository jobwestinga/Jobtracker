"""Service-level graph aggregation, focused on logical-day attribution."""

from datetime import datetime, time, timedelta

from jobtracker.core import timeutils

DAY_START = time(3, 0)


def test_breakdown_attributes_by_logical_day(service, subject):
    # Session at 02:00 -> logical day = previous calendar date (03:00 boundary).
    service.add_session(
        subject.id, datetime(2026, 6, 20, 2, 0), datetime(2026, 6, 20, 2, 30)
    )
    # Session at 04:00 -> logical day = same calendar date.
    service.add_session(
        subject.id, datetime(2026, 6, 20, 4, 0), datetime(2026, 6, 20, 5, 0)
    )

    data = service.get_daily_subject_breakdown(days=None, day_start=DAY_START)
    totals = {d["date"]: d["total_seconds"] for d in data}

    assert totals.get("2026-06-19", 0) == 1800   # the 02:00 session
    assert totals.get("2026-06-20", 0) == 3600   # the 04:00 session


def test_session_2300_to_0200_counts_on_start_logical_day(service, subject):
    # 23:00 -> 02:00 spans midnight but stays in one logical day (the 20th).
    service.add_session(
        subject.id, datetime(2026, 6, 20, 23, 0), datetime(2026, 6, 21, 2, 0)
    )
    data = service.get_daily_subject_breakdown(days=None, day_start=DAY_START)
    totals = {d["date"]: d["total_seconds"] for d in data}

    assert totals.get("2026-06-20", 0) == 3 * 3600
    assert totals.get("2026-06-21", 0) == 0


def test_breakdown_segments_carry_subject_metadata(service, subject):
    service.add_session(
        subject.id, datetime(2026, 6, 20, 4, 0), datetime(2026, 6, 20, 5, 0)
    )
    data = service.get_daily_subject_breakdown(days=None, day_start=DAY_START)
    day = next(d for d in data if d["date"] == "2026-06-20")
    seg = day["segments"][0]
    assert seg["subject_id"] == subject.id
    assert seg["subject_name"] == subject.name
    assert seg["color"] == subject.color
    assert seg["seconds"] == 3600


def test_breakdown_empty_is_zero_filled(service):
    data = service.get_daily_subject_breakdown(days=7, day_start=DAY_START)
    assert len(data) == 7
    assert all(d["total_seconds"] == 0 for d in data)
    assert all(d["segments"] == [] for d in data)


def test_archived_subject_still_appears_in_breakdown(service, subject):
    service.add_session(
        subject.id, datetime(2026, 6, 20, 4, 0), datetime(2026, 6, 20, 5, 0)
    )
    service.archive_subject(subject.id)
    data = service.get_daily_subject_breakdown(days=None, day_start=DAY_START)
    totals = {d["date"]: d["total_seconds"] for d in data}
    assert totals.get("2026-06-20", 0) == 3600


def test_live_active_session_included(service, subject):
    service.start_subject(subject.id)
    # Backdate the active start so there is measurable live time.
    past = (datetime.now() - timedelta(minutes=10)).isoformat()
    service.db.connection.execute(
        "UPDATE sessions SET start_time = ? WHERE id = ?",
        (past, service.active_session.id),
    )
    service.db.connection.commit()
    service.active_session.start_time = past

    day_start = service.get_day_start()
    expected_key = timeutils.logical_day(datetime.fromisoformat(past), day_start).isoformat()
    data = service.get_daily_subject_breakdown(days=None)
    totals = {d["date"]: d["total_seconds"] for d in data}
    assert totals.get(expected_key, 0) >= 540  # ~10 minutes of live time


# ── get_subject_stats filters ────────────────────────────────────────────────
def test_subject_stats_total_counts_all_closed(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 1, 9, 0), datetime(2026, 6, 1, 10, 0))
    service.add_session(subject.id, datetime(2026, 6, 2, 9, 0), datetime(2026, 6, 2, 10, 0))
    assert service.get_subject_stats(subject.id, "Total") == 7200


def test_subject_stats_last_30_days_excludes_old(service, subject):
    old = datetime.now() - timedelta(days=120)
    service.add_session(subject.id, old, old + timedelta(hours=1))
    assert service.get_subject_stats(subject.id, "Last 30 days") == 0
    assert service.get_subject_stats(subject.id, "Total") == 3600


# ── day-start setting persistence ────────────────────────────────────────────
def test_day_start_setting_defaults_to_three_am(service):
    assert service.get_day_start() == time(3, 0)


def test_day_start_setting_roundtrip(service):
    service.set_day_start("05:30")
    assert service.get_day_start() == time(5, 30)
    # Persisted in the settings table.
    assert service.db.get_setting("day_start_time") == "05:30"
