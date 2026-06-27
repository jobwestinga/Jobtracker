"""Stacked-bar labels, threshold tiers, and seamless segment preparation."""

from jobtracker.ui.widgets.graphs_view import (
    _bucket_label,
    _intensity_style,
    _merge_adjacent_segments,
)


def test_week_labels_follow_iso_8601_across_year_boundary():
    assert _bucket_label("2020-12-28", "weekly") == "W53"
    assert _bucket_label("2021-01-04", "weekly") == "W01"


def test_month_and_day_labels_are_specific():
    assert _bucket_label("2026-06-01", "monthly") == "Jun 26"
    assert _bucket_label("2026-06-18", "daily") == "06-18"


def test_weekly_threshold_reduction_reaches_red_near_sixty_hours():
    below = 59 * 3600 / (7 * 0.85)
    above = 60 * 3600 / (7 * 0.85)
    assert _intensity_style(below)[0] == "#FB923C"
    assert _intensity_style(above)[0] == "#EF4444"


def test_monthly_threshold_reduction_reaches_red_above_210_hours():
    at_threshold = 210 * 3600 / (30 * 0.70)
    above = 211 * 3600 / (30 * 0.70)
    assert _intensity_style(at_threshold)[0] == "#FB923C"
    assert _intensity_style(above)[0] == "#EF4444"


def test_consecutive_same_subject_segments_are_merged_only_when_adjacent():
    segments = [
        {"subject_id": 1, "subject_name": "A", "color": "#111", "seconds": 10},
        {"subject_id": 1, "subject_name": "A", "color": "#111", "seconds": 20},
        {"subject_id": 2, "subject_name": "B", "color": "#222", "seconds": 30},
        {"subject_id": 1, "subject_name": "A", "color": "#111", "seconds": 40},
    ]
    merged = _merge_adjacent_segments(segments)
    assert [segment["seconds"] for segment in merged] == [30, 30, 40]
    assert [segment["subject_id"] for segment in merged] == [1, 2, 1]
