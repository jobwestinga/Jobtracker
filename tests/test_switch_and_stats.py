"""Switch-subject behavior and subject stats (batched + live session)."""

from datetime import datetime, timedelta


def _backdate_active_start(database, service, minutes: int) -> None:
    past = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    database.connection.execute(
        "UPDATE sessions SET start_time = ? WHERE id = ?",
        (past, service.active_session.id),
    )
    database.connection.commit()
    service.active_session.start_time = past


# ── switch_subject ───────────────────────────────────────────────────────────
def test_switch_stops_current_and_starts_target(database, service, subject):
    other = service.add_subject("Maths", "#22C55E", "")
    service.start_subject(subject.id)
    _backdate_active_start(database, service, minutes=10)

    assert service.switch_subject(other.id) is True
    assert service.active_subject.id == other.id
    closed = service.get_sessions_for_subject(subject.id)
    assert len(closed) == 1
    assert closed[0].end_time is not None
    assert closed[0].duration_seconds >= 590


def test_switch_to_same_subject_is_refused(database, service, subject):
    service.start_subject(subject.id)
    _backdate_active_start(database, service, minutes=10)
    assert service.switch_subject(subject.id) is False
    # Still tracking, session untouched.
    assert service.active_session is not None
    assert service.active_subject.id == subject.id


def test_switch_to_missing_subject_keeps_current(service, subject):
    service.start_subject(subject.id)
    assert service.switch_subject(999_999) is False
    assert service.active_subject.id == subject.id
    assert service.active_session is not None


def test_switch_when_idle_is_a_plain_start(service, subject):
    assert service.switch_subject(subject.id) is True
    assert service.active_subject.id == subject.id


def test_switch_applies_normal_30s_stop_rule(service, subject):
    other = service.add_subject("Maths", "#22C55E", "")
    service.start_subject(subject.id)
    # Immediate switch -> old session under 30s -> dropped, like a manual stop.
    assert service.switch_subject(other.id) is True
    assert service.get_sessions_for_subject(subject.id) == []
    assert service.active_subject.id == other.id


# ── stats: batched map + live session inclusion ──────────────────────────────
def test_stats_map_matches_per_subject_totals(service, subject):
    other = service.add_subject("Maths", "#22C55E", "")
    service.add_session(
        subject.id, datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 10, 0)
    )
    service.add_session(
        other.id, datetime(2026, 6, 21, 9, 0), datetime(2026, 6, 21, 11, 0)
    )
    totals = service.get_subject_stats_map("Total")
    assert totals[subject.id] == service.get_subject_stats(subject.id, "Total") == 3600
    assert totals[other.id] == service.get_subject_stats(other.id, "Total") == 7200


def test_stats_include_live_session(database, service, subject):
    service.start_subject(subject.id)
    _backdate_active_start(database, service, minutes=10)

    per_subject = service.get_subject_stats(subject.id, "Total")
    assert per_subject >= 590
    totals = service.get_subject_stats_map("Total")
    assert totals[subject.id] >= 590


def test_live_session_respects_window_start_attribution(database, service, subject):
    service.start_subject(subject.id)
    # Session started 10 days ago -> outside "Last 7 days" by start attribution.
    _backdate_active_start(database, service, minutes=10 * 24 * 60)

    assert service.get_subject_stats(subject.id, "Last 7 days") == 0
    assert service.get_subject_stats_map("Last 7 days").get(subject.id, 0) == 0
    # Still fully counted in the all-time view.
    assert service.get_subject_stats(subject.id, "Total") >= 590
