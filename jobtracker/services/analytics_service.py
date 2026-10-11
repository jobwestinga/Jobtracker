"""Logical-day windows, card totals, bar aggregation and agenda data."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import Optional

from ..core import timeutils

def _threshold_period_days(
    grouping: str,
    bucket_date: date,
    today: date,
) -> int:
    """Days used only for a bar's color threshold, not its displayed total."""
    if grouping == "weekly":
        if bucket_date == timeutils.week_start(today):
            return today.weekday() + 1
        return 7
    if grouping == "monthly":
        if (
            bucket_date.year == today.year
            and bucket_date.month == today.month
        ):
            return today.day
        return calendar.monthrange(bucket_date.year, bucket_date.month)[1]
    return 1


class AnalyticsService:
    """Read-only analytics use the facade timer and logical-day settings."""

    def __init__(self, tracker):
        self._tracker = tracker

    def get_earliest_session_date(self) -> Optional[str]:
        return self._tracker.db.get_earliest_session_date()

    def get_latest_session_date(self) -> Optional[str]:
        return self._tracker.db.get_latest_session_date()

    def graph_end_day(self, day_start: Optional[time] = None) -> date:
        """The last logical day any graph should show.

        Normally today, but stretched forward when sessions exist beyond it, so
        work logged ahead of time is visible instead of silently out of range.
        """
        day_start = day_start or self._tracker.get_day_start()
        today = timeutils.logical_day(datetime.now(), day_start)
        latest = timeutils.logical_day_of_iso(
            self._tracker.db.get_latest_session_date(), day_start
        )
        return max(today, latest) if latest is not None else today

    def _stats_since_iso(self, filter_type: str) -> Optional[str]:
        """Window start for a card stats filter; None means all time."""
        now = datetime.now()
        if filter_type == "Today":
            # "Today" honours the configurable logical-day boundary (default 03:00).
            day_start = self._tracker.get_day_start()
            today = timeutils.logical_day(now, day_start)
            start_dt, _ = timeutils.logical_day_bounds(today, day_start)
            return start_dt.isoformat()
        if filter_type == "Last 7 days":
            return (now - timedelta(days=7)).isoformat()
        if filter_type == "Last 30 days":
            return (now - timedelta(days=30)).isoformat()
        return None

    def _live_stats_seconds(self, subject_id: int, since_iso: Optional[str]) -> int:
        """Elapsed seconds of the running session if it belongs to this subject
        and starts inside the window (start attribution, like the bar chart)."""
        if not self._tracker.active_session or self._tracker.active_session.subject_id != subject_id:
            return 0
        if since_iso and self._tracker.active_session.start_time < since_iso:
            return 0
        return self._tracker._elapsed_active_seconds()

    def get_subject_stats(self, subject_id: int, filter_type: str = "Total") -> int:
        since_iso = self._tracker._stats_since_iso(filter_type)
        total = self._tracker.db.get_subject_stats(subject_id, since_iso)
        return total + self._tracker._live_stats_seconds(subject_id, since_iso)

    def get_subject_stats_map(self, filter_type: str = "Total") -> dict[int, int]:
        """Totals for all subjects at once (one query + the live session)."""
        since_iso = self._tracker._stats_since_iso(filter_type)
        totals = self._tracker.db.get_all_subject_stats(since_iso)
        if self._tracker.active_session:
            live_id = self._tracker.active_session.subject_id
            live = self._tracker._live_stats_seconds(live_id, since_iso)
            if live:
                totals[live_id] = totals.get(live_id, 0) + live
        return totals

    def _elapsed_active_seconds(self, reference: Optional[datetime] = None) -> int:
        """Seconds the active session has been running up to ``reference`` (now)."""
        if not self._tracker.active_session:
            return 0
        started_at = timeutils.parse_iso(self._tracker.active_session.start_time)
        if started_at is None:
            return 0
        return timeutils.duration_seconds(started_at, reference or datetime.now())

    def get_daily_subject_breakdown(
        self, days: int | None = 10, day_start: time | None = None
    ) -> list[dict]:
        """Backwards-compatible daily breakdown. See get_subject_breakdown."""
        return self._tracker.get_subject_breakdown(
            grouping="daily", days=days, day_start=day_start
        )

    def _resolve_logical_window(
        self,
        day_start: time,
        days: int | None,
        start_date: date | None,
        end_date: date | None,
        grouping: str = "daily",
    ) -> tuple[date, date]:
        """Resolve the [start_day, end_day] logical-day window for analytics.

        Windows are always ANCHORED on the real today, then stretched forward
        to ``last_day`` when sessions are scheduled beyond it. Anchoring on the
        stretched day instead would slide the whole window into the future and
        silently drop real past days from view.
        """
        today = timeutils.logical_day(datetime.now(), day_start)
        last_day = self._tracker.graph_end_day(day_start)
        if start_date is not None and end_date is not None:
            start_day, end_day = start_date, end_date
            if end_day < start_day:
                start_day, end_day = end_day, start_day
        elif days is not None and grouping == "weekly":
            # Presets represent whole visible buckets: 7 days -> one week,
            # 14 days -> two weeks. This avoids a rolling seven-day window
            # producing two partial weekly bars.
            periods = max(1, (days + 6) // 7)
            end_day = last_day
            start_day = timeutils.week_start(today) - timedelta(weeks=periods - 1)
        elif days is not None and grouping == "monthly":
            # Likewise, short rolling ranges should not straddle two month bars.
            periods = max(1, (days + 29) // 30)
            end_day = last_day
            start_day = timeutils.month_start(today)
            for _ in range(periods - 1):
                start_day = (start_day - timedelta(days=1)).replace(day=1)
        elif days is not None:
            end_day = last_day
            start_day = today - timedelta(days=max(1, days) - 1)
        else:  # "All time"
            earliest = timeutils.logical_day_of_iso(
                self._tracker.db.get_earliest_session_date(), day_start
            )
            if self._tracker.active_session:
                active_day = timeutils.logical_day_of_iso(
                    self._tracker.active_session.start_time, day_start
                )
                if active_day is not None and (earliest is None or active_day < earliest):
                    earliest = active_day
            start_day = earliest or today
            end_day = last_day
        return start_day, end_day

    def get_subject_breakdown(
        self,
        grouping: str = "daily",
        days: int | None = 10,
        day_start: time | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict]:
        """Return stacked work data grouped daily / weekly / monthly.

        Every session is attributed to the logical day of its START time (default
        boundary 03:00), then rolled up into the requested ``grouping`` bucket
        (day, ISO-Monday week, or month). A 23:00 -> 02:00 session counts on the
        day it began. The currently running session is included as a live segment.

        Window selection (in priority order):
          • explicit ``start_date``/``end_date`` (custom range)
          • last ``days`` logical days
          • all time (``days=None``)
        """
        day_start = day_start or self._tracker.get_day_start()
        now = datetime.now()

        start_day, end_day = self._tracker._resolve_logical_window(
            day_start, days, start_date, end_date, grouping
        )

        # Ordered, de-duplicated bucket keys spanning the window.
        bucket_keys: list[str] = []
        seen: set[str] = set()
        for logical in timeutils.logical_day_range(start_day, end_day):
            key = timeutils.bucket_key(logical, grouping).isoformat()
            if key not in seen:
                seen.add(key)
                bucket_keys.append(key)

        buckets: dict[str, list[dict]] = {key: [] for key in bucket_keys}
        subjects = {s.id: s for s in self._tracker.get_all_subjects_including_archived() if s.id is not None}

        first_start_dt, _ = timeutils.logical_day_bounds(start_day, day_start)
        _, last_end_dt = timeutils.logical_day_bounds(end_day, day_start)
        sessions = self._tracker.db.get_all_closed_sessions_in_range(
            first_start_dt.isoformat(), last_end_dt.isoformat()
        )

        def _bucket_for(start_iso: str) -> str | None:
            logical = timeutils.logical_day_of_iso(start_iso, day_start)
            if logical is None:
                return None
            key = timeutils.bucket_key(logical, grouping).isoformat()
            return key if key in buckets else None

        for sess in sessions:
            key = _bucket_for(sess.start_time)
            if key is None:
                continue
            subject = subjects.get(sess.subject_id)
            if not subject or sess.duration_seconds <= 0:
                continue
            buckets[key].append(
                {
                    "subject_id": sess.subject_id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "seconds": sess.duration_seconds,
                    "start_time": sess.start_time,
                    "end_time": sess.end_time,
                }
            )

        # Include the currently running session as a live segment.
        if self._tracker.active_session:
            subject = subjects.get(self._tracker.active_session.subject_id)
            key = _bucket_for(self._tracker.active_session.start_time)
            if subject is not None and key is not None:
                live_seconds = self._tracker._elapsed_active_seconds(now)
                if live_seconds > 0:
                    buckets[key].append(
                        {
                            "subject_id": self._tracker.active_session.subject_id,
                            "subject_name": subject.name,
                            "color": subject.color,
                            "seconds": live_seconds,
                            "start_time": self._tracker.active_session.start_time,
                            "end_time": now.isoformat(),
                        }
                    )

        result = []
        for key in bucket_keys:
            total_seconds = sum(segment["seconds"] for segment in buckets[key])
            bucket_date = date.fromisoformat(key)
            if grouping == "weekly":
                period_days = 7
            elif grouping == "monthly":
                period_days = calendar.monthrange(
                    bucket_date.year, bucket_date.month
                )[1]
            else:
                period_days = 1
            threshold_days = _threshold_period_days(
                grouping,
                bucket_date,
                timeutils.logical_day(now, day_start),
            )
            threshold_factor = {
                "weekly": 0.85,
                "monthly": 0.70,
            }.get(grouping, 1.0)
            result.append(
                {
                    "date": key,
                    "total_seconds": total_seconds,
                    # Weekly labels reach each daily intensity tier at 85% of
                    # the straight seven-day threshold; monthly labels at 70%.
                    "intensity_seconds": (
                        total_seconds / (threshold_days * threshold_factor)
                    ),
                    "period_days": period_days,
                    "threshold_days": threshold_days,
                    "threshold_factor": threshold_factor,
                    "segments": buckets[key],
                }
            )
        return result

    def get_agenda_data(
        self,
        start_day: date,
        end_day: date,
        day_start: time | None = None,
    ) -> tuple[list[str], list[dict]]:
        """Return (logical_day_keys, sessions) for the agenda timeline.

        Each session carries ``start_h`` / ``end_h`` as agenda hours within its
        logical day (see :func:`timeutils.agenda_hour`): after-midnight work that
        belongs to the previous logical day lands at 24..27 so it renders at the
        bottom of that day rather than padding the top. Sessions that run past the
        logical-day end are clamped to the day's lower edge.
        """
        day_start = day_start or self._tracker.get_day_start()
        subjects = {s.id: s for s in self._tracker.get_all_subjects_including_archived() if s.id is not None}
        day_keys = [d.isoformat() for d in timeutils.logical_day_range(start_day, end_day)]
        day_key_set = set(day_keys)
        day_end_hour = 24.0 + day_start.hour + day_start.minute / 60.0

        first_start_dt, _ = timeutils.logical_day_bounds(start_day, day_start)
        _, last_end_dt = timeutils.logical_day_bounds(end_day, day_start)
        sessions = self._tracker.db.get_all_closed_sessions_in_range(
            first_start_dt.isoformat(), last_end_dt.isoformat()
        )

        result: list[dict] = []

        def _emit(session_id, subject, start_dt, end_dt, duration):
            logical = timeutils.logical_day(start_dt, day_start)
            key = logical.isoformat()
            if key not in day_key_set:
                return
            start_h = timeutils.agenda_hour(start_dt, day_start)
            if timeutils.logical_day(end_dt, day_start) > logical:
                end_h = day_end_hour  # spilled past this logical day; clamp to edge
            else:
                end_h = timeutils.agenda_hour(end_dt, day_start)
            if end_h <= start_h:
                end_h = min(day_end_hour, start_h + (1.0 / 60.0))
            result.append(
                {
                    "session_id": session_id,
                    "day": key,
                    "subject_id": subject.id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "start_h": start_h,
                    "end_h": end_h,
                    "duration_seconds": duration,
                }
            )

        for sess in sessions:
            subject = subjects.get(sess.subject_id)
            start_dt = timeutils.parse_iso(sess.start_time)
            end_dt = timeutils.parse_iso(sess.end_time)
            if not subject or start_dt is None or end_dt is None:
                continue
            _emit(sess.id, subject, start_dt, end_dt, sess.duration_seconds)

        if self._tracker.active_session:
            subject = subjects.get(self._tracker.active_session.subject_id)
            start_dt = timeutils.parse_iso(self._tracker.active_session.start_time)
            if subject is not None and start_dt is not None:
                now = datetime.now()
                _emit(None, subject, start_dt, now, self._tracker._elapsed_active_seconds(now))

        return day_keys, result

    def get_sessions_for_logical_day(
        self, day: date, day_start: time | None = None
    ) -> list[dict]:
        """All closed sessions belonging to a single logical day, with subject
        metadata. Used when a day is opened from a graph."""
        day_start = day_start or self._tracker.get_day_start()
        subjects = {s.id: s for s in self._tracker.get_all_subjects_including_archived() if s.id is not None}
        start_dt, end_dt = timeutils.logical_day_bounds(day, day_start)
        rows = self._tracker.db.get_all_closed_sessions_in_range(start_dt.isoformat(), end_dt.isoformat())
        result: list[dict] = []
        for sess in rows:
            subject = subjects.get(sess.subject_id)
            if not subject:
                continue
            result.append(
                {
                    "session_id": sess.id,
                    "subject_id": sess.subject_id,
                    "subject_name": subject.name,
                    "color": subject.color,
                    "start_time": sess.start_time,
                    "end_time": sess.end_time,
                    "duration_seconds": sess.duration_seconds,
                }
            )
        if self._tracker.active_session:
            active_start = timeutils.parse_iso(self._tracker.active_session.start_time)
            subject = subjects.get(self._tracker.active_session.subject_id)
            if (
                active_start is not None
                and subject is not None
                and timeutils.logical_day(active_start, day_start) == day
            ):
                now = datetime.now()
                result.append(
                    {
                        "session_id": None,
                        "subject_id": self._tracker.active_session.subject_id,
                        "subject_name": subject.name,
                        "color": subject.color,
                        "start_time": self._tracker.active_session.start_time,
                        "end_time": None,
                        "duration_seconds": self._tracker._elapsed_active_seconds(now),
                    }
                )
        return result
