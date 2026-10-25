"""Graph range, grouping and agenda hour-band resolution shared by frontends.

The Qt graphs page and the native engine both turn persisted graph settings into
the same logical-day window. Date math stays in ``core.timeutils``; this module
only composes it with the tracker's own end-day and agenda queries.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from ..core.timeutils import (
    GROUPING_CHOICES,
    agenda_hour,
    agenda_hour_window,
    graph_preset_window,
    grouping_for_preset,
    grouping_for_span,
    logical_day,
    parse_iso,
    resolve_grouping,
)

logger = logging.getLogger("jobtracker")

PRESETS = ("weeks", "months", "year", "all")
_LEGACY_RANGES = {"7": "weeks", "14": "weeks", "30": "months"}
_RANGE_LABELS = {"weeks": "Weeks", "months": "Months", "year": "Year", "all": "All Time"}


def load_range_preset(get_setting: Callable[[str, str], str]) -> str:
    raw = get_setting("graph_range", "weeks")
    resolved = _LEGACY_RANGES.get(raw, raw)
    return resolved if resolved in PRESETS else "weeks"


def load_custom_range(get_setting: Callable[[str, str], str]) -> tuple[date, date] | None:
    start_raw = get_setting("graph_custom_start", "")
    end_raw = get_setting("graph_custom_end", "")
    if get_setting("graph_range", "weeks") != "custom" or not start_raw or not end_raw:
        return None
    try:
        return date.fromisoformat(start_raw), date.fromisoformat(end_raw)
    except ValueError:
        return None


def load_grouping(get_setting: Callable[[str, str], str]) -> str:
    """Persisted bucket-size override; "auto" means follow the range."""
    value = get_setting("graph_grouping", "auto")
    return value if value in GROUPING_CHOICES else "auto"


def load_hour(get_setting: Callable[[str, str], str], key: str, default: int) -> int:
    try:
        return min(27, max(0, int(get_setting(key, str(default)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class GraphPreferences:
    preset: str = "weeks"
    custom_range: Optional[tuple[date, date]] = None
    grouping: str = "auto"
    hour_start: int = 6
    hour_end: int = 23
    autofit_hours: bool = False

    @classmethod
    def from_settings(cls, get_setting: Callable[[str, str], str]) -> "GraphPreferences":
        hour_start = load_hour(get_setting, "graph_hour_start", 6)
        hour_end = load_hour(get_setting, "graph_hour_end", 23)
        if hour_end <= hour_start:
            hour_start, hour_end = 6, 23
        return cls(
            preset=load_range_preset(get_setting),
            custom_range=load_custom_range(get_setting),
            grouping=load_grouping(get_setting),
            hour_start=hour_start,
            hour_end=hour_end,
            autofit_hours=get_setting("graph_autofit_hours", "0") == "1",
        )

    def replace(self, **changes) -> "GraphPreferences":
        updated = dataclasses.replace(self, **changes)
        if updated.preset not in PRESETS:
            raise ValueError("preset must be weeks, months, year or all")
        if updated.grouping not in GROUPING_CHOICES:
            raise ValueError("grouping must be one of " + ", ".join(GROUPING_CHOICES))
        if not (isinstance(updated.hour_start, int) and isinstance(updated.hour_end, int)
                and 0 <= updated.hour_start < updated.hour_end <= 27):
            raise ValueError("hours must satisfy 0 <= start < end <= 27")
        if updated.custom_range is not None and updated.custom_range[0] > updated.custom_range[1]:
            raise ValueError("custom range must end on or after its start")
        if not isinstance(updated.autofit_hours, bool):
            raise ValueError("autofit_hours must be true or false")
        return updated


def window_and_grouping(service, prefs: GraphPreferences, now: Optional[datetime] = None) -> tuple[date | None, date | None, str]:
    """(start_date, end_date, grouping). Dates are None for all-history."""
    if prefs.custom_range:
        start_day, end_day = prefs.custom_range
        return start_day, end_day, resolve_grouping(grouping_for_span(start_day, end_day), prefs.grouping)
    day_start = service.get_day_start()
    # Anchor the preset on the real today, then stretch only the END so
    # sessions scheduled ahead are visible. Anchoring on a future day would
    # slide the whole window forward and hide real past days.
    today = logical_day(now or datetime.now(), day_start)
    window = graph_preset_window(prefs.preset, today)
    grouping = resolve_grouping(grouping_for_preset(prefs.preset), prefs.grouping)
    if window is not None:
        end_day = max(window[1], service.graph_end_day(day_start))
        return window[0], end_day, grouping
    return None, None, grouping


def range_label(prefs: GraphPreferences) -> str:
    if prefs.custom_range:
        start_day, end_day = prefs.custom_range
        return f"{start_day.isoformat()} → {end_day.isoformat()}"
    return _RANGE_LABELS.get(prefs.preset, "Weeks")


@dataclass(frozen=True)
class AgendaWindow:
    start_day: date
    end_day: date
    day_keys: list
    sessions: list
    hour_start: int
    hour_end: int
    now_marker: tuple[str, float]


def agenda_window(service, prefs: GraphPreferences, now: Optional[datetime] = None) -> AgendaWindow:
    """Agenda days, sessions and the visible hour band for the preferences."""
    now = now or datetime.now()
    day_start = service.get_day_start()
    today = logical_day(now, day_start)
    start_day, end_day, _grouping = window_and_grouping(service, prefs, now)
    if start_day is None or end_day is None:
        earliest_iso = service.get_earliest_session_date()
        parsed = parse_iso(earliest_iso) if earliest_iso else None
        last_day = service.graph_end_day(day_start)
        start_day = logical_day(parsed, day_start) if parsed else last_day
        end_day = last_day
    day_keys, sessions = service.get_agenda_data(start_day, end_day, day_start)

    # Auto-fit the visible hour band to actual work (in agenda-hour space, so
    # late-night work can push the bottom edge past 24 -> "(+1)").
    hour_start, hour_end = prefs.hour_start, prefs.hour_end
    if prefs.autofit_hours and sessions:
        try:
            min_h = math.floor(min(s["start_h"] for s in sessions))
            max_h = math.ceil(max(s["end_h"] for s in sessions))
            if min_h < max_h:
                hour_start, hour_end = agenda_hour_window(min_h, max_h)
        except Exception:
            logger.exception("Agenda auto-fit failed; using configured hours")
    return AgendaWindow(
        start_day, end_day, day_keys, sessions, hour_start, hour_end,
        (today.isoformat(), agenda_hour(now, day_start)),
    )
