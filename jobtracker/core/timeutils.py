"""
Centralized time and logical-day handling for JobTracker.

This is the ONE place responsible for:
  • Parsing / formatting datetimes that are stored in the database.
  • Calculating durations.
  • Determining the "logical day" a timestamp belongs to.
  • Handling a configurable day-start time (default 03:00).
  • Attributing / splitting sessions across logical-day boundaries for analytics.

────────────────────────────────────────────────────────────────────────────
CURRENT TIME MODEL — read this before changing anything
────────────────────────────────────────────────────────────────────────────
  • All session timestamps (`start_time`, `end_time`, `last_active_at`) are stored
    as NAIVE LOCAL ISO-8601 strings produced by ``datetime.now().isoformat()``.
  • There is NO timezone or UTC handling yet. Durations are wall-clock differences
    between two naive local datetimes. This means a session that spans a daylight
    saving transition will be off by ±1 hour. This is a known, accepted limitation
    for a single-user, single-machine, local macOS app.
  • ``created_at`` columns use SQLite ``CURRENT_TIMESTAMP`` (UTC) but are only used
    as sort tiebreakers, never for analytics — so the UTC/local mismatch is benign.
  • A future UTC migration should be funneled THROUGH this module so that call
    sites (services / UI) never have to change. Keep all date math here.

────────────────────────────────────────────────────────────────────────────
LOGICAL DAY
────────────────────────────────────────────────────────────────────────────
  A "logical day" starts at ``day_start`` (default 03:00), not midnight, because
  for the user a day ends when they sleep, not exactly at midnight.

  A timestamp whose clock time is BEFORE ``day_start`` belongs to the PREVIOUS
  calendar date. Example with day_start = 03:00:

      2026-06-20 02:30  ->  logical day 2026-06-19
      2026-06-20 03:00  ->  logical day 2026-06-20
      2026-06-20 23:00  ->  logical day 2026-06-20

  Therefore a session 23:00 → 02:00 stays entirely within ONE logical day.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

# Default logical day boundary. The user's day ends when they sleep, ~03:00.
DEFAULT_DAY_START: time = time(3, 0)


# ── Parsing / formatting ─────────────────────────────────────────────────────
def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ISO datetime string, returning None on any failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def to_iso(moment: datetime) -> str:
    """Format a datetime the same way it is stored in the database."""
    return moment.isoformat()


def now_iso() -> str:
    """Current local timestamp in the stored format."""
    return datetime.now().isoformat()


# ── Durations ────────────────────────────────────────────────────────────────
def duration_seconds(start: Optional[datetime], end: Optional[datetime]) -> int:
    """Whole seconds between two datetimes, clamped to >= 0.

    Returns 0 if either value is missing. Never returns a negative number, so
    reversed start/end pairs collapse to 0 rather than corrupting totals.
    """
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds()))


def duration_seconds_iso(start_iso: Optional[str], end_iso: Optional[str]) -> int:
    """Convenience wrapper for two stored ISO strings."""
    return duration_seconds(parse_iso(start_iso), parse_iso(end_iso))


# ── Day-start setting ────────────────────────────────────────────────────────
def parse_day_start(value) -> time:
    """Parse a stored ``day_start_time`` setting ("HH:MM") into a ``time``.

    Falls back to :data:`DEFAULT_DAY_START` for anything unparseable.
    """
    if isinstance(value, time):
        return value
    if not value:
        return DEFAULT_DAY_START
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).time()
        except (ValueError, TypeError):
            continue
    return DEFAULT_DAY_START


def day_start_to_str(value: time) -> str:
    """Serialize a ``time`` for storage in the settings table."""
    return value.strftime("%H:%M")


# ── Logical day ──────────────────────────────────────────────────────────────
def logical_day(moment: datetime, day_start: time = DEFAULT_DAY_START) -> date:
    """Return the logical day a timestamp belongs to.

    Times before ``day_start`` roll back to the previous calendar date.
    """
    result = moment.date()
    if moment.time() < day_start:
        result = result - timedelta(days=1)
    return result


def logical_day_of_iso(
    value: Optional[str], day_start: time = DEFAULT_DAY_START
) -> Optional[date]:
    """Logical day for a stored ISO string, or None if it can't be parsed."""
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return logical_day(parsed, day_start)


def logical_day_bounds(
    day: date, day_start: time = DEFAULT_DAY_START
) -> Tuple[datetime, datetime]:
    """Half-open [start, end) datetime range covering a single logical day."""
    start = datetime.combine(day, day_start)
    end = start + timedelta(days=1)
    return start, end


def logical_day_range(
    start_day: date, end_day: date
) -> List[date]:
    """Inclusive list of logical days from ``start_day`` to ``end_day``."""
    if end_day < start_day:
        return [start_day]
    span = (end_day - start_day).days
    return [start_day + timedelta(days=i) for i in range(span + 1)]


def split_by_logical_day(
    start: datetime,
    end: datetime,
    day_start: time = DEFAULT_DAY_START,
) -> List[Tuple[date, int]]:
    """Split a [start, end) interval into (logical_day, seconds) chunks.

    A session that crosses a logical-day boundary is divided so each logical day
    receives only the seconds that actually fall within it. Used by analytics
    that need precise per-day attribution (e.g. a future heatmap / export
    summary). Returns an empty list for non-positive intervals.

    NOTE: the current bar-chart aggregation attributes a whole session to the
    logical day of its START. This helper is the precise alternative and is kept
    tested so it can be adopted without re-deriving the math.
    """
    if end <= start:
        return []

    chunks: List[Tuple[date, int]] = []
    cursor = start
    while cursor < end:
        day = logical_day(cursor, day_start)
        _, day_end = logical_day_bounds(day, day_start)
        segment_end = min(end, day_end)
        seconds = int((segment_end - cursor).total_seconds())
        if seconds > 0:
            chunks.append((day, seconds))
        cursor = segment_end
    return chunks
