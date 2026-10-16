"""Small dependency-free weight chart calculations, using naive calendar days."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from . import timeutils


@dataclass(frozen=True)
class WeightPoint:
    x: float
    y: float
    row: dict | None = None


def points_for(rows, *, start=None, end=None, historic=False):
    """Filter inclusive calendar dates, keeping measurement times and sources."""
    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        moment = timeutils.parse_iso(row.get("measured_at"))
        if isinstance(row.get("weight_kg"), bool):
            continue
        try:
            value = float(row.get("weight_kg"))
        except (TypeError, ValueError, OverflowError):
            continue
        if moment is None or not math.isfinite(value) or not 0 < value <= 1000:
            continue
        if (not historic and row.get("source") == "historic") or (start and moment.date() < start) or (end and moment.date() > end):
            continue
        clock = moment.hour * 3600 + moment.minute * 60 + moment.second + moment.microsecond / 1e6
        points.append(WeightPoint(moment.toordinal() + clock / 86400, value, row))
    return sorted(points, key=lambda point: point.x)


def range_start(end: date, days: int) -> date:
    """Inclusive trailing calendar window, safe at the minimum ISO date."""
    return date.fromordinal(max(1, end.toordinal() - max(1, days) + 1))


def date_label(ordinal: float, pattern="%d %b %Y") -> str:
    return date.fromordinal(min(date.max.toordinal(), max(1, math.floor(ordinal)))).strftime(pattern)


def year_ticks(first: float, last: float) -> list[tuple[int, int]]:
    """January 1 coordinates for the separate year axis, in calendar ordinals."""
    if not math.isfinite(first) or not math.isfinite(last) or first > last:
        return []
    start = date.fromordinal(min(date.max.toordinal(), max(1, math.floor(first)))).year
    end = date.fromordinal(min(date.max.toordinal(), max(1, math.floor(last)))).year
    return [(day, year) for year in range(start, end + 1)
            if first <= (day := date(year, 1, 1).toordinal()) <= last]


def _unique(points):
    values = []
    for point in sorted(points, key=lambda item: item.x):
        if not math.isfinite(point.x) or not math.isfinite(point.y):
            continue
        if values and point.x == values[-1][0]:
            x, y, count = values[-1]
            values[-1] = (x, y + (point.y - y) / (count + 1), count + 1)
        else:
            values.append((point.x, point.y, 1))
    return [WeightPoint(x, y) for x, y, _ in values]


def loess(points, span=0.04):
    """Local linear tricube smoothing, matching the original graph's LOWESS."""
    values = _unique(points)
    if len(values) < 3:
        return values
    span = float(span)
    span = min(1, max(0, span)) if math.isfinite(span) else 0.04
    count = max(2, min(len(values), math.floor(len(values) * span + 1e-10)))
    left, result = 0, []
    for point in values:
        while left + count < len(values) and point.x - values[left].x > values[left + count].x - point.x:
            left += 1
        radius = max(point.x - values[left].x, values[left + count - 1].x - point.x)
        sw = sx = sy = sxx = sxy = 0.0
        for other in values[left:left + count]:
            x = other.x - point.x
            weight = (1 - min(1, abs(x) / radius) ** 3) ** 3 if radius else 1
            sw += weight
            sx += weight * x
            sy += weight * other.y
            sxx += weight * x * x
            sxy += weight * x * other.y
        determinant = sw * sxx - sx * sx
        y = (sy * sxx - sx * sxy) / determinant if abs(determinant) > 1e-12 else (sy / sw if sw else point.y)
        result.append(WeightPoint(point.x, y))
    return result


def rolling_mean(points, days=7):
    """Average actual readings in a calendar window; missing days add nothing."""
    values, result = _unique(points), []
    left, total = 0, 0.0
    for right, point in enumerate(values):
        total += point.y
        cutoff = math.floor(point.x) - max(1, days) + 1
        while left < right and math.floor(values[left].x) < cutoff:
            total -= values[left].y
            left += 1
        result.append(WeightPoint(point.x, total / (right - left + 1)))
    return result


def pchip(points, until=None, samples=120):
    """Shape-preserving interpolation through sparse historic measurements."""
    knots = _unique(points)
    if len(knots) < 2:
        return knots
    h = [b.x - a.x for a, b in zip(knots, knots[1:])]
    slopes = [(b.y - a.y) / width for a, b, width in zip(knots, knots[1:], h)]

    def edge(h0, h1, d0, d1):
        value = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if value * d0 <= 0:
            return 0.0
        return 3 * d0 if d0 * d1 <= 0 and abs(value) > abs(3 * d0) else value

    derivatives = [0.0] * len(knots)
    derivatives[0] = slopes[0] if len(knots) == 2 else edge(h[0], h[1], slopes[0], slopes[1])
    derivatives[-1] = slopes[-1] if len(knots) == 2 else edge(h[-1], h[-2], slopes[-1], slopes[-2])
    for i in range(1, len(knots) - 1):
        if slopes[i - 1] * slopes[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            derivatives[i] = (w1 + w2) / (w1 / slopes[i - 1] + w2 / slopes[i])
    end = min(until if until is not None else knots[-1].x, knots[-1].x)
    if end <= knots[0].x:
        return knots[:1]
    count, interval, result = max(2, min(10000, int(samples))), 0, []
    for i in range(count):
        x = knots[0].x + (end - knots[0].x) * i / (count - 1)
        while interval < len(knots) - 2 and x > knots[interval + 1].x:
            interval += 1
        t = (x - knots[interval].x) / h[interval]
        t2, t3 = t * t, t * t * t
        y = (2 * t3 - 3 * t2 + 1) * knots[interval].y + (t3 - 2 * t2 + t) * h[interval] * derivatives[interval]
        y += (-2 * t3 + 3 * t2) * knots[interval + 1].y + (t3 - t2) * h[interval] * derivatives[interval + 1]
        result.append(WeightPoint(x, y))
    return result


def trend_for(points, mode="loess", span=0.04):
    if mode == "none":
        return []
    if mode == "mean7":
        return rolling_mean(points)
    dense = [point for point in points if not point.row or point.row.get("source") != "historic"]
    historic = [point for point in points if point.row and point.row.get("source") == "historic"]
    if not dense:
        return pchip(historic)
    fitted = loess(dense, span)
    old = [point for point in historic if point.x < fitted[0].x]
    return pchip(old + fitted[:20], until=fitted[0].x) + fitted if old else fitted
