"""
Active-session recovery decision logic (pure, UI-free).

When the app restarts and finds an unfinished (open) session, we must NOT assume
the entire elapsed time was work — the laptop may have slept for hours. This
module decides whether to prompt the user and computes the numbers the prompt
needs. The actual Qt dialog is a thin shell over this.

"Last known active time" comes from the heartbeat written ~once/minute while a
session runs (``sessions.last_active_at``). If the app was only closed briefly
(small gap) we keep the existing behaviour and don't bother the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from . import timeutils

# Prompt only when the gap between "last known active" and now is at least this
# large. Below it, a brief close, the elapsed time is trustworthy enough to keep.
DEFAULT_GAP_PROMPT_SECONDS = 300  # 5 minutes

# Recovery choices.
CHOICE_LAST = "last"            # end at last known active time
CHOICE_NOW = "now"              # end now
CHOICE_CUSTOM_END = "custom_end"      # user-picked end datetime
CHOICE_CUSTOM_LENGTH = "custom_length"  # user-picked length from start


@dataclass
class RecoveryInfo:
    """Everything the recovery prompt needs to display and act on."""

    session_id: int
    subject_name: str
    start: datetime
    last_active: datetime
    now: datetime

    @property
    def duration_if_last_seconds(self) -> int:
        """Work duration if we end the session at the last known active time."""
        return timeutils.duration_seconds(self.start, self.last_active)

    @property
    def duration_if_now_seconds(self) -> int:
        """Work duration if we end the session at the current time."""
        return timeutils.duration_seconds(self.start, self.now)

    @property
    def gap_seconds(self) -> int:
        """Unaccounted gap between last known active time and now."""
        return max(0, int((self.now - self.last_active).total_seconds()))


def build_recovery_info(
    session,
    subject_name: str,
    now: Optional[datetime] = None,
    gap_threshold_seconds: int = DEFAULT_GAP_PROMPT_SECONDS,
) -> Optional[RecoveryInfo]:
    """Decide whether to prompt for a recovered session.

    Returns a :class:`RecoveryInfo` when the user should be asked, or ``None``
    when the gap is small enough that the session can be resumed/closed without
    interruption. Never mutates or deletes anything.
    """
    if session is None:
        return None

    start = timeutils.parse_iso(getattr(session, "start_time", None))
    if start is None:
        return None

    # Fall back to the start time for legacy sessions that predate heartbeats.
    last_active = timeutils.parse_iso(getattr(session, "last_active_at", None)) or start

    now = now or datetime.now()

    # Clamp last_active into a sane window: never before start, never after now.
    if last_active < start:
        last_active = start
    if last_active > now:
        last_active = now

    gap = (now - last_active).total_seconds()
    if gap < gap_threshold_seconds:
        return None

    return RecoveryInfo(
        session_id=getattr(session, "id", None),
        subject_name=subject_name,
        start=start,
        last_active=last_active,
        now=now,
    )


def end_time_for_choice(
    info: RecoveryInfo,
    choice: str,
    custom_end: Optional[datetime] = None,
    custom_length_seconds: Optional[int] = None,
) -> datetime:
    """Resolve a recovery choice into the concrete end datetime to store.

    Falls back to "now" for unknown/empty inputs so a session is never left open.
    """
    if choice == CHOICE_LAST:
        return info.last_active
    if choice == CHOICE_NOW:
        return info.now
    if choice == CHOICE_CUSTOM_END and custom_end is not None:
        return custom_end
    if choice == CHOICE_CUSTOM_LENGTH and custom_length_seconds is not None:
        return info.start + timedelta(seconds=max(0, int(custom_length_seconds)))
    return info.now
