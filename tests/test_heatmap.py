"""Heatmap aggregation: tracked seconds per logical day."""

from datetime import date, datetime, time

DAY_START = time(3, 0)


def _by_date(rows):
    return {r["date"]: r["total_seconds"] for r in rows}


def test_daily_aggregation(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0))
    service.add_session(subject.id, datetime(2026, 6, 20, 14, 0), datetime(2026, 6, 20, 15, 0))
    rows = service.get_heatmap_data(
        day_start=DAY_START, start_date=date(2026, 6, 20), end_date=date(2026, 6, 20)
    )
    assert _by_date(rows)["2026-06-20"] == 2 * 3600


def test_late_night_counts_previous_logical_day(service, subject):
    # 23:00 -> 02:00 belongs entirely to logical day 06-20.
    service.add_session(subject.id, datetime(2026, 6, 20, 23, 0), datetime(2026, 6, 21, 2, 0))
    rows = service.get_heatmap_data(
        day_start=DAY_START, start_date=date(2026, 6, 20), end_date=date(2026, 6, 21)
    )
    totals = _by_date(rows)
    assert totals["2026-06-20"] == 3 * 3600
    assert totals["2026-06-21"] == 0


def test_session_before_3am(service, subject):
    # 01:00 -> 02:00 on the 21st -> logical day 06-20.
    service.add_session(subject.id, datetime(2026, 6, 21, 1, 0), datetime(2026, 6, 21, 2, 0))
    rows = service.get_heatmap_data(
        day_start=DAY_START, start_date=date(2026, 6, 20), end_date=date(2026, 6, 21)
    )
    totals = _by_date(rows)
    assert totals["2026-06-20"] == 3600
    assert totals["2026-06-21"] == 0


def test_empty_days_are_zero(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0))
    rows = service.get_heatmap_data(
        day_start=DAY_START, start_date=date(2026, 6, 18), end_date=date(2026, 6, 22)
    )
    totals = _by_date(rows)
    assert totals["2026-06-19"] == 0
    assert totals["2026-06-21"] == 0
    assert totals["2026-06-20"] == 3600
    assert len(rows) == 5  # contiguous span, zero-filled


def test_all_history_range_includes_earliest_to_today(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 10, 9, 0), datetime(2026, 6, 10, 10, 0))
    rows = service.get_heatmap_data(day_start=DAY_START)  # all history
    totals = _by_date(rows)
    assert totals.get("2026-06-10", 0) == 3600
    # The earliest day is the first cell.
    assert rows[0]["date"] == "2026-06-10"


def test_sessions_for_logical_day(service, subject):
    service.add_session(subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0))
    # Late-night session belonging to 06-20.
    service.add_session(subject.id, datetime(2026, 6, 21, 1, 0), datetime(2026, 6, 21, 2, 0))
    sessions = service.get_sessions_for_logical_day(date(2026, 6, 20), DAY_START)
    assert len(sessions) == 2
    assert all(s["subject_name"] == subject.name for s in sessions)


def test_all_history_includes_backdated_live_session(service, subject):
    service.start_subject(subject.id)
    backdated = datetime(2026, 6, 10, 9, 0).isoformat()
    service.db.connection.execute(
        "UPDATE sessions SET start_time = ?, last_active_at = ? WHERE id = ?",
        (backdated, backdated, service.active_session.id),
    )
    service.db.connection.commit()
    service.active_session.start_time = backdated

    rows = service.get_heatmap_data(day_start=DAY_START)
    assert rows[0]["date"] == "2026-06-10"
    assert _by_date(rows)["2026-06-10"] > 0
