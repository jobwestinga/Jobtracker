"""Native chart parity against synthetic scipy/statsmodels reference fits."""

import json
import math
from datetime import date
from pathlib import Path

import pytest

from jobtracker.core.weight_plot import (
    WeightPoint, loess, pchip, points_for, range_start, rolling_mean, trend_for, year_ticks,
)

REFERENCE = json.loads((Path(__file__).parent / "mobile/fixtures/weight_math.json").read_text())


@pytest.mark.parametrize("case", REFERENCE["loess"], ids=lambda case: case["name"])
def test_native_loess_matches_original_reference(case):
    # A large ordinal tests the same subtraction used for real calendar dates.
    points = [WeightPoint(700000 + x, y) for x, y in zip(case["days"], case["weights"])]
    assert [point.y for point in loess(points, case["span"])] == pytest.approx(case["expected"], abs=1e-7)


@pytest.mark.parametrize("case", REFERENCE["pchip"], ids=lambda case: case["name"])
def test_native_historic_curve_matches_original_reference(case):
    points = [WeightPoint(x, y) for x, y in zip(case["days"], case["weights"])]
    assert [point.y for point in pchip(points, samples=case["samples"])] == pytest.approx(case["expected"], abs=1e-9)


def test_native_historic_bridge_matches_original_curve():
    case = REFERENCE["historic"]
    historic = [WeightPoint(x, y, {"source": "historic"}) for x, y in zip(case["historic_days"], case["historic_weights"])]
    dense = [WeightPoint(x, y, {"source": "scale"}) for x, y in zip(case["dense_days"], case["dense_weights"])]
    fitted = trend_for(historic + dense, span=case["span"])
    assert [point.y for point in fitted[:120]] == pytest.approx(case["expected"], abs=1e-9)
    assert [point.y for point in fitted[120:]] == pytest.approx(case["dense_expected"], abs=1e-9)


def test_native_chart_filters_calendar_days_not_logical_days():
    rows = [
        {"measured_at": "2026-10-10T01:00:00", "weight_kg": 80, "source": "manual"},
        {"measured_at": "2026-10-09T23:00:00", "weight_kg": 81, "source": "scale"},
        {"measured_at": "2026-10-10T02:00:00", "weight_kg": 60, "source": "historic"},
        {"measured_at": "invalid", "weight_kg": 80},
        {"measured_at": "2026-10-10T01:00:00Z", "weight_kg": 80},
        {"measured_at": "2026-10-10", "weight_kg": float("inf")},
    ]
    points = points_for(rows, start=date(2026, 10, 10), end=date(2026, 10, 10))
    assert [point.y for point in points] == [80]
    assert math.floor(points[0].x) == date(2026, 10, 10).toordinal()
    assert len(points_for(rows, historic=True)) == 3


def test_native_calendar_rolling_mean_ignores_missing_days_and_clock_spacing():
    points = [WeightPoint(10.99, 80), WeightPoint(11.01, 82), WeightPoint(16.99, 84), WeightPoint(50, 90)]
    assert [point.y for point in rolling_mean(points)] == pytest.approx([80, 81, 82, 90])


def test_duplicate_knots_single_points_and_empty_history_are_safe():
    duplicate = [WeightPoint(10, 80), WeightPoint(10, 82)]
    assert loess(duplicate) == [WeightPoint(10, 81)]
    assert pchip(duplicate) == [WeightPoint(10, 81)]
    assert trend_for([]) == []
    assert trend_for(duplicate, "none") == []
    assert pchip([WeightPoint(0, 80), WeightPoint(10, 82)], until=-1) == [WeightPoint(0, 80)]


def test_plot_ranges_include_both_ends_and_support_minimum_iso_date():
    assert range_start(date(2026, 10, 10), 30) == date(2026, 9, 11)
    assert range_start(date.min, 365) == date.min


def test_year_grid_uses_calendar_boundaries_and_inclusive_endpoints():
    ticks = year_ticks(date(2023, 12, 3).toordinal(), date(2026, 1, 1).toordinal())
    assert ticks == [(date(year, 1, 1).toordinal(), year) for year in (2024, 2025, 2026)]
    assert ticks[1][0] - ticks[0][0] == 366
    assert year_ticks(date(99, 12, 31).toordinal(), date(100, 1, 1).toordinal()) == [(date(100, 1, 1).toordinal(), 100)]
    assert year_ticks(date.min.toordinal(), date.min.toordinal()) == [(1, 1)]
    assert year_ticks(float("nan"), 10) == []
    assert year_ticks(10, 1) == []
