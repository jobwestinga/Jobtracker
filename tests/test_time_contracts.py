"""Boundary scenarios also consumed by the phone's display-time regression suite."""
import json
from datetime import datetime, time
from pathlib import Path

import pytest
from jobtracker.core import timeutils

CASES = json.loads((Path(__file__).parent / "contracts/time_boundaries.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["now"] + "_" + case["boundary"])
def test_tracking_day_and_agenda_position_follow_shared_boundary(case):
    now = datetime.fromisoformat(case["now"])
    boundary = time.fromisoformat(case["boundary"])
    day = timeutils.logical_day(now, boundary)
    assert day.isoformat() == case["logical_day"]
    assert timeutils.agenda_hour(now, boundary) == pytest.approx(case["hour"])
