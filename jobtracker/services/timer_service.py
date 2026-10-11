"""Timer actions and device-scoped recovery behind TrackerService.

TrackerService owns active_session and active_subject. This component holds only
its facade reference and always dispatches nested actions through that facade,
so synced mutations retain their existing transaction and outbox boundaries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..core.models import Session, Subject
from ..core import timeutils, recovery

logger = logging.getLogger("jobtracker")


class TimerService:
    """Timer use cases share the facade state and Database.atomic boundary."""

    def __init__(self, tracker):
        self._tracker = tracker

    def _owned_open_sessions(self) -> list[Session]:
        """Open sessions this install is allowed to touch, newest first.

        A session stamped with ANOTHER device's id belongs to that device's
        timer — the phone's running session must survive the Mac starting up.
        A NULL device_id means "no owner recorded" (historical rows, imported
        backups, sessions from before this column existed), which this install
        adopts so old data keeps recovering exactly as it used to.
        """
        return [
            s
            for s in self._tracker.db.get_open_sessions()
            if s.device_id is None or s.device_id == self._tracker.device_id
        ]

    def _adopt_open_session(self, *, include_other_devices: bool = False) -> None:
        """Refresh the timer from storage, without closing anything.

        Server reads show the newest timer from any device. Recovery remains
        device-scoped, so displaying a foreign timer never claims ownership.
        """
        owned = (
            self._tracker.db.get_open_sessions()
            if include_other_devices
            else self._tracker._owned_open_sessions()
        )
        self._tracker.active_session = owned[0] if owned else None
        self._tracker.active_subject = (
            self._tracker.db.get_subject(self._tracker.active_session.subject_id)
            if self._tracker.active_session
            else None
        )

    def recover_open_sessions(self) -> None:
        """Reconcile sessions left open by a crash. Destructive — see __init__.

        Non-destructive for the primary session: it is resumed, never deleted.
        Sessions owned by another device are left completely alone.
        """
        owned = self._tracker._owned_open_sessions()
        foreign = len(self._tracker.db.get_open_sessions()) - len(owned)
        if foreign:
            logger.info(
                "Recovery: leaving %d open session(s) owned by another device alone",
                foreign,
            )

        self._tracker.active_session = owned[0] if owned else None
        self._tracker.active_subject = None

        # Close stale parallel open sessions to keep a single active timer
        # invariant. The newest open session is kept as active. Any unexpected
        # extras are closed but explicitly preserved even when shorter than the
        # normal 30-second minimum: unfinished recovery data is never deleted.
        if len(owned) > 1:
            now = datetime.now()
            for stale in owned[1:]:
                if stale.id is None:
                    continue
                started_at = timeutils.parse_iso(stale.start_time)
                last_active = timeutils.parse_iso(stale.last_active_at) or started_at
                if started_at is None:
                    end_at = now
                else:
                    end_at = min(now, max(started_at, last_active or started_at))
                duration = timeutils.duration_seconds(started_at, end_at)
                self._tracker.db.close_recovered_session(stale.id, end_at, duration)
                logger.info(
                    "Recovery: closed stale parallel open session %s at its "
                    "last-known-active time (%ds)",
                    stale.id,
                    duration,
                )

        if self._tracker.active_session:
            self._tracker.active_subject = self._tracker.db.get_subject(self._tracker.active_session.subject_id)
            # If the subject was deleted while a session was open, close it.
            if self._tracker.active_subject is None:
                if self._tracker.active_session.id is not None:
                    self._tracker.db.close_recovered_session(
                        self._tracker.active_session.id, datetime.now(), 0
                    )
                logger.info("Recovery: active session had no subject; closed it")
                self._tracker.active_session = None
            else:
                logger.info(
                    "Recovery: resumed active session %s for subject '%s'",
                    self._tracker.active_session.id, self._tracker.active_subject.name,
                )

    def _set_active_subject(self, subject: Optional[Subject]) -> None:
        self._tracker.active_subject = subject

    def start_subject(
        self, subject_id: int, start_time: Optional[datetime] = None,
        *, allow_parallel: bool = False,
    ) -> bool:
        """Start at the captured client time, preserving offline work.

        ``allow_parallel`` is for authoritative replay of an already recorded
        client session. Normal interactive starts still require an idle timer.
        """
        if start_time is not None and not timeutils.is_local_datetime(start_time):
            return False
        self._tracker._adopt_open_session()
        if not allow_parallel and self._tracker.db.get_open_sessions():
            return False
        subject = self._tracker.db.get_subject(subject_id)
        if subject is None or (subject.is_archived and not allow_parallel):
            return False
        try:
            # Stamp the owner so another device's recovery never closes this.
            self._tracker.active_session = self._tracker.db.start_session(
                subject_id, device_id=self._tracker.device_id, start_time=start_time
            )
        except ValueError:
            return False
        if self._tracker.active_session.id is None:
            self._tracker.active_session = None
            return False
        self._tracker._set_active_subject(self._tracker.db.get_subject(subject_id))
        if self._tracker.active_subject is None:
            self._tracker.db.stop_session(self._tracker.active_session.id, datetime.now(), 0)
            self._tracker.active_session = None
            return False
        return True

    def _persisted_active_session(self) -> Optional[Session]:
        """Resolve the exact cached timer after a sync or another writer.

        A full mirror refresh can change local integer ids. The uid survives;
        using only the old id can overwrite an unrelated, newly inserted row.
        """
        active = self._tracker.active_session
        if active is None:
            return None
        session_id = (
            self._tracker.db.id_for_uid("sessions", active.uid)
            if active.uid else active.id
        )
        persisted = self._tracker.db.get_session(session_id) if session_id is not None else None
        if persisted is None or persisted.end_time is not None:
            self._tracker._adopt_open_session()
            return None
        self._tracker.active_session = persisted
        self._tracker.active_subject = self._tracker.db.get_subject(persisted.subject_id)
        return persisted

    def stop_active_subject(self, end_time: Optional[datetime] = None) -> None:
        """Stop the active session. Ends now unless an explicit ``end_time`` is
        given (used by crash-recovery to end at a chosen point in the past)."""
        session = self._tracker._persisted_active_session()
        if session is None:
            return
        if session.id is None:
            self._tracker.active_session = None
            self._tracker._set_active_subject(None)
            return
        end_dt = end_time or datetime.now()
        start_dt = timeutils.parse_iso(session.start_time)
        if (
            start_dt is None or not timeutils.is_local_datetime(end_dt)
            or end_dt < start_dt
        ):
            # A delayed stop or malformed clock must never turn real work into
            # a zero-second session and delete it under the accidental-tap rule.
            logger.warning("Refusing invalid end time for active session %s", session.id)
            return
        duration = timeutils.duration_seconds(start_dt, end_dt)
        self._tracker.db.stop_session(session.id, end_dt, duration)
        self._tracker.active_session = None
        self._tracker._set_active_subject(None)

    def switch_subject(self, subject_id: int, moment: Optional[datetime] = None) -> bool:
        """Stop the active session (normal stop rules) and start ``subject_id``.

        Refuses to "switch" to the subject already being tracked. Works as a
        plain start when nothing is active.
        """
        self._tracker._adopt_open_session()
        if self._tracker.active_subject and self._tracker.active_subject.id == subject_id:
            return False
        subject = self._tracker.db.get_subject(subject_id)
        if subject is None or subject.is_archived:
            return False
        moment = moment or datetime.now()
        if not timeutils.is_local_datetime(moment):
            return False
        try:
            with self._tracker.db.atomic():
                if self._tracker.active_session:
                    self._tracker.stop_active_subject(moment)
                    if self._tracker.active_session is not None:
                        return False
                if not self._tracker.start_subject(subject_id, moment):
                    # Keep the original session if the destination disappears
                    # or its insert fails after we stopped the old one.
                    raise ValueError("switch destination could not be started")
            return True
        except Exception as exc:
            self._tracker._adopt_open_session()
            if isinstance(exc, ValueError):
                return False
            raise

    def get_active_recovery_info(
        self, now: Optional[datetime] = None, gap_threshold_seconds: Optional[int] = None
    ):
        """Return RecoveryInfo if the resumed active session needs a recovery
        prompt (large gap since last known active), else None."""
        if not self._tracker.active_session or not self._tracker.active_subject:
            return None
        threshold = (
            recovery.DEFAULT_GAP_PROMPT_SECONDS
            if gap_threshold_seconds is None
            else gap_threshold_seconds
        )
        return recovery.build_recovery_info(
            self._tracker.active_session,
            self._tracker.active_subject.name,
            now=now,
            gap_threshold_seconds=threshold,
        )

    def resolve_recovery(
        self, end_time: datetime, *, session_id: Optional[int] = None
    ) -> None:
        """Close the recovered active session at the user-chosen end time."""
        session = self._tracker._persisted_active_session()
        if session is None or (session_id is not None and session.id != session_id):
            return
        start = timeutils.parse_iso(session.start_time)
        if start is None or not timeutils.is_local_datetime(end_time) or end_time < start:
            return
        logger.info(
            "Recovery resolved: ending session %s at %s",
            self._tracker.active_session.id if self._tracker.active_session else None,
            end_time.isoformat(),
        )
        # Legacy sessions can have no heartbeat: "last active" is then their
        # start. Keep even a zero-second recovery record so choosing the safe
        # default cannot silently delete an hour of unfinished work.
        self._tracker.db.close_recovered_session(
            session.id, end_time, timeutils.duration_seconds(start, end_time)
        )
        self._tracker._adopt_open_session()

    def heartbeat_active_session(self, moment: Optional[datetime] = None) -> None:
        """Record that the active session is still legitimately running.

        Called ~once per minute by the UI. Cheap single UPDATE, no history.
        Safe no-op when nothing is being tracked.
        """
        session = self._tracker._persisted_active_session()
        if session is None or session.id is None:
            return
        if moment is not None and not timeutils.is_local_datetime(moment):
            return
        when_iso = timeutils.to_iso(moment) if moment is not None else timeutils.now_iso()
        self._tracker.db.touch_active_session(session.id, when_iso)
        self._tracker.active_session.last_active_at = when_iso
