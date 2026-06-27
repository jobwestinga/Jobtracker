"""Pure recovery decision logic (gap threshold + choice -> end time)."""

from datetime import datetime, timedelta

from jobtracker.core import recovery
from jobtracker.core.models import Session


def _session(start, last_active=None, sid=1):
    return Session(
        id=sid, task_id=1, start_time=start, end_time=None,
        duration_seconds=0, note=None, last_active_at=last_active,
    )


def test_no_prompt_for_small_gap():
    start = datetime(2026, 6, 20, 9, 0)
    last = datetime(2026, 6, 20, 9, 40)
    now = last + timedelta(seconds=60)  # 1 min gap
    info = recovery.build_recovery_info(
        _session(start.isoformat(), last.isoformat()), "Physics", now=now,
        gap_threshold_seconds=300,
    )
    assert info is None


def test_prompt_for_large_gap():
    start = datetime(2026, 6, 20, 9, 0)
    last = datetime(2026, 6, 20, 9, 40)
    now = datetime(2026, 6, 20, 15, 0)  # >5h gap
    info = recovery.build_recovery_info(
        _session(start.isoformat(), last.isoformat()), "Physics", now=now,
        gap_threshold_seconds=300,
    )
    assert info is not None
    assert info.subject_name == "Physics"
    assert info.duration_if_last_seconds == 40 * 60
    assert info.duration_if_now_seconds == 6 * 3600
    assert info.gap_seconds == int((now - last).total_seconds())


def test_missing_last_active_falls_back_to_start():
    start = datetime(2026, 6, 20, 9, 0)
    now = datetime(2026, 6, 20, 12, 0)
    info = recovery.build_recovery_info(
        _session(start.isoformat(), None), "Physics", now=now, gap_threshold_seconds=300
    )
    assert info is not None
    # With no heartbeat, last known active == start, so "end at last" is 0s.
    assert info.duration_if_last_seconds == 0
    assert info.gap_seconds == 3 * 3600


def test_last_active_clamped_into_window():
    start = datetime(2026, 6, 20, 9, 0)
    # last_active accidentally after now -> clamped to now.
    bad_last = datetime(2026, 6, 20, 20, 0)
    now = datetime(2026, 6, 20, 12, 0)
    info = recovery.build_recovery_info(
        _session(start.isoformat(), bad_last.isoformat()), "X", now=now, gap_threshold_seconds=300
    )
    # Gap collapses to 0 -> below threshold -> no prompt.
    assert info is None


def test_choice_end_times():
    start = datetime(2026, 6, 20, 9, 0)
    last = datetime(2026, 6, 20, 9, 40)
    now = datetime(2026, 6, 20, 15, 0)
    info = recovery.build_recovery_info(
        _session(start.isoformat(), last.isoformat()), "P", now=now, gap_threshold_seconds=300
    )

    assert recovery.end_time_for_choice(info, recovery.CHOICE_LAST) == last
    assert recovery.end_time_for_choice(info, recovery.CHOICE_NOW) == now

    custom = datetime(2026, 6, 20, 10, 30)
    assert recovery.end_time_for_choice(info, recovery.CHOICE_CUSTOM_END, custom_end=custom) == custom

    by_len = recovery.end_time_for_choice(
        info, recovery.CHOICE_CUSTOM_LENGTH, custom_length_seconds=3600
    )
    assert by_len == start + timedelta(hours=1)


def test_choice_defaults_to_now_when_underspecified():
    start = datetime(2026, 6, 20, 9, 0)
    last = datetime(2026, 6, 20, 9, 40)
    now = datetime(2026, 6, 20, 15, 0)
    info = recovery.build_recovery_info(
        _session(start.isoformat(), last.isoformat()), "P", now=now, gap_threshold_seconds=300
    )
    # custom end requested but none supplied -> safe fallback to now.
    assert recovery.end_time_for_choice(info, recovery.CHOICE_CUSTOM_END) == now
