"""The engine host: one command thread owning the store, timer and scheduling.

Threads and connections
    * The command thread (``run``) owns the injected ``Database`` connection and
      the facade's timer. Every command, scheduled job and sync completion runs
      here, one at a time.
    * A reader thread only splits stdin into frames and enqueues them; it blocks
      when the bounded inbox is full, which applies backpressure to the client.
    * The sync worker opens its own connection inside its own thread and posts
      results back through the inbox. Nothing outside the command thread uses
      the foreground connection.

Lifecycle
    Normal quit follows the Qt window's rules (refuse while genuinely tracking,
    bounded push, rotating JSON safety copy). Losing the parent (stdin EOF)
    stops accepting commands, lets an in-flight sync settle and exits without
    stopping the timer or writing anything: the unfinished session is recovered
    by the next owner exactly as after a crash.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Callable, Optional

from ..core.auto_backup import write_auto_backup
from ..services.tracker_service import TrackerService
from ..sync import settings as sync_settings
from ..sync import state as sync_state
from ..sync.presentation import describe_status
from ..sync.service import SyncedTrackerService
from . import commands, dto, protocol, receipts

logger = logging.getLogger("jobtracker")

HEARTBEAT_SECONDS = 60
SYNC_INTERVAL_SECONDS = 5 * 60
SYNC_LAUNCH_DELAY = 1.2
PUSH_DELAY = 1.5
MAX_FOLLOW_UPS = 3
QUIT_SYNC_WAIT = 3.0
EOF_SETTLE_WAIT = 8.0
INBOX_LIMIT = protocol.MAX_OUTSTANDING
DATA_TOPICS = ("subjects", "timer", "sessions", "graphs", "goals", "templates", "weight", "settings", "appearance")


def build_service(database, *, recover: bool = True):
    """The same facade selection and recovery as the Qt window's startup."""
    service = TrackerService(database, recover=False)
    if sync_settings.is_enabled(service):
        return SyncedTrackerService(database, device_id=service.device_id, recover=recover), True
    if recover:
        service.recover_open_sessions()
    return service, False


class EngineHost:
    def __init__(
        self, service, *, sync_on: bool, mode: str, backups_dir: Path,
        write: Callable[[bytes], None], sync_factory: Optional[Callable] = None,
        clock: Callable[[], datetime] = datetime.now, receipts_backup: Optional[Path] = None,
    ):
        self.service = service
        self.sync_on = sync_on
        self.mode = mode
        self.backups_dir = backups_dir
        self.instance_id = protocol.new_instance_id()
        self.revision = 0
        self.receipts_backup = receipts_backup
        self._write_bytes = write
        self._write_lock = threading.Lock()
        self._clock = clock
        self._inbox: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=INBOX_LIMIT)
        self._timers: dict[str, float] = {}
        self._extra_topics: set[str] = set()
        self._accepting = True
        self._running = False
        self._exit_code = 0
        self._foreground = True
        self._follow_ups = 0
        self._last_pending: Optional[int] = None
        self._sync_result = None
        self._sync_configuration = self._read_sync_configuration()
        info = service.get_active_recovery_info() if service.active_session is not None else None
        self._recovery_uid = service.active_session.uid if info is not None else None
        self._sync = None
        if sync_on and sync_settings.is_configured(service):
            factory = sync_factory or _default_sync_factory
            self._sync = factory(service.db.db_path, service.device_id, self._post_sync_result)

    # ── construction from a selected store ───────────────────────────────
    @classmethod
    def for_selected_store(cls, selection, *, write, sync_factory=None) -> "EngineHost":
        """Reuse the eager compatibility singleton, which already owns the
        selected store's connection, rather than opening a second owner."""
        from ..core import database as database_module
        from .bootstrap import canonical

        shared = database_module.db
        if canonical(Path(shared.db_path)) != selection.db_path:
            raise RuntimeError("The database singleton does not own the selected store")
        backup = receipts.install(shared.connection, backup_dir=selection.data_dir / "backups" / "migrations")
        service, sync_on = build_service(shared)
        host = cls(
            service, sync_on=sync_on, mode=selection.mode,
            backups_dir=selection.data_dir / "backups", write=write,
            sync_factory=sync_factory, receipts_backup=backup,
        )
        host._generate_due_goals()
        return host

    # ── output ───────────────────────────────────────────────────────────
    def emit(self, message: dict) -> None:
        message.setdefault("protocol_version", protocol.PROTOCOL_VERSION)
        message.setdefault("engine_instance", self.instance_id)
        try:
            frame = protocol.encode(message)
        except protocol.FrameError as exc:
            frame = protocol.encode({
                "protocol_version": protocol.PROTOCOL_VERSION, "engine_instance": self.instance_id,
                "kind": protocol.KIND_RESPONSE, "request_id": message.get("request_id"),
                "outcome": protocol.INVALID, "error": str(exc), "result": {}, "topics": [],
                "revision": self.revision,
            })
        with self._write_lock:
            self._write_bytes(frame)

    def hello(self) -> dict:
        return {
            "kind": protocol.KIND_HELLO,
            "mode": self.mode,
            "store_id": self.service.device_id,
            "db_path": str(self.service.db.db_path),
            "pid": os.getpid(),
            "revision": self.revision,
            "sync_enabled": self.sync_on,
            "commands": commands.describe(),
            "receipts_backup": str(self.receipts_backup) if self.receipts_backup else None,
            "qt_loaded": any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules),
        }

    def info(self) -> dict:
        info = self.hello()
        info.pop("kind")
        info["engine_instance"] = self.instance_id
        return info

    # ── command execution ────────────────────────────────────────────────
    def handle_frame(self, raw: bytes) -> None:
        request_id = None
        try:
            message = protocol.decode(raw)
            request_id = message.get("request_id") if isinstance(message.get("request_id"), str) else None
            request_id, name, params = protocol.validate_command(message)
        except protocol.FrameError as exc:
            self._respond(request_id, protocol.INVALID, {}, error=str(exc))
            return
        if not self._accepting:
            self._respond(request_id, protocol.UNAVAILABLE, {}, error="The engine is stopping")
            return
        command = commands.REGISTRY.get(name)
        if command is None:
            self._respond(request_id, protocol.INVALID, {}, error=f"Unknown command {name}")
            return
        self.execute(request_id, command, params)

    def execute(self, request_id: str, command: commands.Command, params: dict) -> dict:
        self._extra_topics = set()
        if command.kind == commands.MUTATION:
            outcome, result, error = self._mutate(request_id, command, params)
        else:
            outcome, result, error = self._run_plain(command, params)
        topics: list[str] = []
        if outcome in (protocol.APPLIED,) or (command.kind == commands.ACTION and outcome == protocol.OK):
            topics = sorted(set(command.topics) | self._extra_topics)
            if command.kind == commands.MUTATION:
                self.revision += 1
        message = self._respond(request_id, outcome, result, error=error, topics=topics)
        if topics:
            self.emit({"kind": protocol.KIND_EVENT, "event": "changed", "topics": topics, "revision": self.revision})
        if command.kind == commands.MUTATION and outcome == protocol.APPLIED:
            self._after_local_write()
        return message

    def _mutate(self, request_id, command, params):
        connection = self.service.db.connection
        digest = receipts.fingerprint(command.name, params, self.service.device_id)
        try:
            with self.service.db.atomic():
                saved = receipts.lookup(connection, request_id, digest)
                if saved is not None:
                    outcome, result = saved
                    return protocol.REPLAYED, {**result, "original_outcome": outcome}, None
                try:
                    with self.service.db.atomic():
                        result = command.handler(self, params)
                    outcome, error = protocol.APPLIED, None
                except commands.Missing as exc:
                    outcome, result, error = protocol.MISSING, {"reason": str(exc)}, str(exc)
                except commands.Refused as exc:
                    outcome, result, error = protocol.REFUSED, {"reason": str(exc)}, str(exc)
                if outcome != protocol.APPLIED:
                    self.service._adopt_open_session()
                receipts.store(connection, request_id, command.name, digest, outcome, result)
            return outcome, result, error
        except receipts.ReceiptConflict as exc:
            return protocol.CONFLICT, {}, str(exc)
        except commands.Invalid as exc:
            self.service._adopt_open_session()
            return protocol.INVALID, {}, str(exc)
        except (sqlite3.Error, OSError) as exc:
            logger.exception("Command %s failed to persist", command.name)
            self._safe_readopt()
            return protocol.STORAGE_ERROR, {}, f"The change was not saved: {exc}"
        except Exception as exc:  # noqa: BLE001 - never leave a half-applied runtime
            logger.exception("Command %s failed", command.name)
            self._safe_readopt()
            return protocol.STORAGE_ERROR, {}, f"The change was not saved: {exc}"

    def _run_plain(self, command, params):
        try:
            return protocol.OK, command.handler(self, params), None
        except commands.Missing as exc:
            return protocol.MISSING, {"reason": str(exc)}, str(exc)
        except commands.Refused as exc:
            return protocol.REFUSED, {"reason": str(exc)}, str(exc)
        except commands.Invalid as exc:
            return protocol.INVALID, {}, str(exc)
        except (sqlite3.Error, OSError) as exc:
            logger.exception("Command %s failed", command.name)
            return protocol.STORAGE_ERROR, {}, str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Command %s failed", command.name)
            return protocol.STORAGE_ERROR, {}, str(exc)

    def _safe_readopt(self) -> None:
        try:
            self.service._adopt_open_session()
        except Exception:  # noqa: BLE001
            logger.exception("Could not re-read the timer after a failed command")

    def _respond(self, request_id, outcome, result, *, error=None, topics=()) -> dict:
        message = {
            "kind": protocol.KIND_RESPONSE,
            "request_id": request_id,
            "outcome": outcome,
            "result": result,
            "topics": list(topics),
            "revision": self.revision,
        }
        if error:
            message["error"] = error
        self.emit(message)
        return message

    # ── context used by command handlers ─────────────────────────────────
    def extra_topics(self, topics) -> None:
        self._extra_topics |= set(topics)

    def timer_snapshot(self) -> dict:
        if self._recovery_uid is not None:
            active = self.service.active_session
            if active is None or active.uid != self._recovery_uid:
                self._recovery_uid = None
        return dto.timer(self.service, now=self._clock(), recovery_pending=self._recovery_uid is not None)

    def recovery_settled(self) -> None:
        self._recovery_uid = None

    def foreground_changed(self, active: bool) -> None:
        self._foreground = active
        if active:
            self.request_sync()

    def request_quit(self) -> dict:
        service = self.service
        genuinely_tracking = service.active_session is not None and service.get_active_recovery_info() is None
        if genuinely_tracking:
            return {
                "quit": False,
                "reason": "active_session",
                "title": "Session Still Active",
                "message": "JobTracker cannot quit while a session is active.\n\nUse the red Stop Tracking button first.",
            }
        try:
            if self.request_sync() and self._sync is not None:
                self._sync.wait_for_idle(QUIT_SYNC_WAIT)
        except Exception:  # noqa: BLE001
            logger.exception("Sync on quit failed")
        backup_error = None
        backup_path = None
        try:
            backup_path = str(write_auto_backup(service.export_data(), self.backups_dir))
        except Exception as exc:  # noqa: BLE001 - a backup problem never traps the user
            logger.exception("Auto-backup on quit failed")
            backup_error = str(exc)
        self._accepting = False
        self._running = False
        return {"quit": True, "backup_path": backup_path, "backup_error": backup_error}

    # ── sync ─────────────────────────────────────────────────────────────
    def _read_sync_configuration(self) -> tuple[bool, str, str]:
        return (
            sync_settings.is_enabled(self.service),
            sync_settings.server_url(self.service),
            sync_settings.read_token(),
        )

    def sync_configuration_changed(self) -> None:
        self._sync_configuration = self._read_sync_configuration()

    def request_sync(self) -> bool:
        if not self._accepting or self._sync is None:
            return False
        enabled, url, token = self._sync_configuration
        if not enabled or not url or not token:
            return False
        started = self._sync.request(url, token)
        if started:
            self._emit_sync_status()
        return started

    def _post_sync_result(self, result) -> None:
        # Called on the worker thread: only hand the result to the command thread.
        self._inbox.put(("sync_done", result))

    def _on_sync_done(self, result) -> None:
        self._sync_result = result
        if result.applied or result.deleted or result.repaired:
            self.service._adopt_open_session()
            self.revision += 1
            self.emit({"kind": protocol.KIND_EVENT, "event": "changed", "topics": list(DATA_TOPICS), "revision": self.revision})
        pending = self._pending_count()
        if self._sync is not None and self._sync.finished():
            self.request_sync()
        elif result.ok and pending and self._follow_ups < MAX_FOLLOW_UPS:
            self._follow_ups += 1
            self._schedule("push", PUSH_DELAY)
        elif not pending:
            self._follow_ups = 0
        if not result.ok and not result.offline:
            logger.warning("Sync problem: %s", result.error)
        self._emit_sync_status()

    def _pending_count(self) -> Optional[int]:
        try:
            return sync_state.pending_count(self.service.db.connection)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return 0
            return None
        except sqlite3.DatabaseError:
            return None

    def _after_local_write(self) -> None:
        pending = self._pending_count()
        previous = self._last_pending
        self._last_pending = pending
        if pending and (previous is None or pending > previous):
            self._follow_ups = 0
            self._schedule("push", PUSH_DELAY)
        self._emit_sync_status()

    def sync_status(self) -> dict:
        enabled, url, token = self._sync_configuration
        connection = self.service.db.connection
        pending = self._pending_count()
        status_error = "" if pending is not None else "Sync status could not be read. Open Settings to retry."
        try:
            last_sync_at = sync_state.get(connection, sync_state.LAST_SYNC_AT)
            last_error = sync_state.get(connection, sync_state.LAST_ERROR)
        except sqlite3.Error:
            last_sync_at = last_error = ""
        busy = bool(self._sync is not None and self._sync.busy)
        text = describe_status(
            enabled=enabled, url=url, token=token, pending=pending or 0,
            last_sync_at=last_sync_at, last_error=last_error, result=self._sync_result,
            busy=busy, connected=bool(self.sync_on and self._sync is not None),
            status_error=status_error,
        )
        return {
            "enabled": enabled,
            "server_url": url,
            "has_token": bool(token),
            "connected": bool(self.sync_on and self._sync is not None),
            "busy": busy,
            "pending": pending or 0,
            "last_sync_at": last_sync_at,
            "title": text.title,
            "detail": text.detail,
            "action": text.action,
            "error": text.error,
        }

    def _emit_sync_status(self) -> None:
        self.emit({"kind": protocol.KIND_EVENT, "event": "sync_status", "status": self.sync_status()})

    # ── scheduled work ───────────────────────────────────────────────────
    def _schedule(self, name: str, delay: float) -> None:
        self._timers[name] = time.monotonic() + delay

    def _schedule_recurring_boundary(self) -> None:
        now = self._clock()
        boundary = datetime.combine(now.date(), self.service.get_day_start())
        if boundary <= now:
            boundary += timedelta(days=1)
        self._schedule("recurring", max(1.0, (boundary - now).total_seconds()))

    def _run_due_timers(self) -> None:
        now = time.monotonic()
        for name, deadline in sorted(self._timers.items(), key=lambda item: item[1]):
            if deadline > now:
                continue
            del self._timers[name]
            try:
                self._fire(name)
            except Exception:  # noqa: BLE001 - one failed job must not stop the engine
                logger.exception("Scheduled job %s failed", name)

    def _fire(self, name: str) -> None:
        if name == "heartbeat":
            self._schedule("heartbeat", HEARTBEAT_SECONDS)
            # A pending recovery prompt keeps its last-known activity: a minute
            # tick must not silently validate the whole gap.
            if self._recovery_uid is None:
                self.service.heartbeat_active_session()
        elif name in ("sync_periodic",):
            self._schedule("sync_periodic", SYNC_INTERVAL_SECONDS)
            self.request_sync()
        elif name in ("sync_launch", "push"):
            self.request_sync()
        elif name == "recurring":
            self._generate_due_goals(notify=True)
            self._schedule_recurring_boundary()

    def _generate_due_goals(self, *, notify: bool = False) -> None:
        try:
            with self.service.db.atomic():
                created = self.service.generate_due_goal_instances()
        except Exception:  # noqa: BLE001
            logger.exception("Recurring goal generation failed")
            self._safe_readopt()
            return
        if notify:
            self.revision += 1
            self.emit({"kind": protocol.KIND_EVENT, "event": "changed", "topics": ["goals"], "revision": self.revision})
            self._after_local_write()
        elif created:
            self.revision += 1

    def next_timeout(self) -> Optional[float]:
        if not self._timers:
            return None
        return max(0.0, min(self._timers.values()) - time.monotonic())

    # ── main loop ────────────────────────────────────────────────────────
    def start_reader(self, stream: BinaryIO) -> threading.Thread:
        def read() -> None:
            reader = protocol.LineReader()
            try:
                while True:
                    chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                    if not chunk:
                        break
                    for item in reader.feed(chunk):
                        if isinstance(item, protocol.FrameError):
                            self._inbox.put(("bad_frame", item))
                        else:
                            self._inbox.put(("frame", item))
            except (OSError, ValueError):
                logger.exception("Command pipe failed")
            self._inbox.put(("eof", None))

        thread = threading.Thread(target=read, name="jobtracker-commands", daemon=True)
        thread.start()
        return thread

    def run(self, stream: BinaryIO) -> int:
        self._running = True
        self.emit(self.hello())
        self._schedule("heartbeat", HEARTBEAT_SECONDS)
        self._schedule_recurring_boundary()
        if self._sync is not None:
            self._schedule("sync_launch", SYNC_LAUNCH_DELAY)
            self._schedule("sync_periodic", SYNC_INTERVAL_SECONDS)
        self._last_pending = self._pending_count()
        self.start_reader(stream)
        while self._running:
            try:
                kind, payload = self._inbox.get(timeout=self.next_timeout())
            except queue.Empty:
                self._run_due_timers()
                continue
            if kind == "frame":
                self.handle_frame(payload)
            elif kind == "bad_frame":
                self._respond(None, protocol.INVALID, {}, error=str(payload))
            elif kind == "sync_done":
                self._on_sync_done(payload)
            elif kind == "eof":
                self._owner_lost()
            self._run_due_timers()
        return self._exit_code

    def request_stop(self) -> None:
        self._inbox.put(("eof", None))

    def _owner_lost(self) -> None:
        """The UI went away: keep every unfinished session and queued write."""
        logger.info("Native client disconnected; settling owned work before exit")
        self._accepting = False
        if self._sync is not None and not self._sync.wait_for_idle(EOF_SETTLE_WAIT):
            logger.warning("Sync pass still running at exit; its outbox stays durable")
        self._running = False


def _default_sync_factory(db_path, device_id, on_done):
    from ..sync.thread_worker import ThreadSyncController

    return ThreadSyncController(db_path, device_id, on_done)
