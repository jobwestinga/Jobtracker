"""Boundary scenarios also consumed by the phone's display-time regression suite."""
import json
from datetime import datetime, time
from pathlib import Path

import pytest
from jobtracker.core import timeutils

FIXTURE = json.loads((Path(__file__).parent / "contracts/time_boundaries.json").read_text())
CASES = FIXTURE["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["now"] + "_" + case["boundary"])
def test_tracking_day_and_agenda_position_follow_shared_boundary(case):
    now = datetime.fromisoformat(case["now"])
    boundary = time.fromisoformat(case["boundary"])
    day = timeutils.logical_day(now, boundary)
    assert day.isoformat() == case["logical_day"]
    assert timeutils.agenda_hour(now, boundary) == pytest.approx(case["hour"])


@pytest.mark.parametrize("case", FIXTURE["agenda_windows"], ids=lambda case: case["id"])
def test_agenda_autofit_contains_the_configured_logical_day(service, subject, case):
    from datetime import date
    from jobtracker.services.graph_window import GraphPreferences, agenda_window

    service.set_day_start(case["boundary"])
    saved = service.add_session(subject.id, datetime.fromisoformat(case["start"]), datetime.fromisoformat(case["end"]))
    day = date.fromisoformat(case["day"])
    prefs = GraphPreferences(custom_range=(day, day), autofit_hours=True)
    window = agenda_window(service, prefs)

    assert window.day_keys == [case["day"]]
    assert len(window.sessions) == 1
    block = window.sessions[0]
    assert block["day"] == case["day"]
    assert (block["start_h"], block["end_h"]) == (case["start_h"], case["end_h"])
    assert (window.hour_start, window.hour_end) == (case["hour_start"], case["hour_end"])
    assert window.hour_start <= block["start_h"] < block["end_h"] <= window.hour_end
    # This is a display-only clamp: history, precision, and start attribution stay exact.
    assert service.get_session(saved.id) == saved
    buckets = service.get_subject_breakdown(start_date=day, end_date=day)
    assert sum(bucket["total_seconds"] for bucket in buckets) == saved.duration_seconds
