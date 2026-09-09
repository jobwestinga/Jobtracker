"""The JobTracker API — the one authoritative writer.

Runs the same ``TrackerService`` the desktop app runs (``jobtracker.core`` and
``jobtracker.services`` import no Qt), so a rule like milestone-gated completion
or the sub-30-second stop is executed in exactly one place for every client.

Endpoint groups:
  /health           liveness, no auth
  /ops              the only write path — idempotent, uid-addressed
  /sync/pull        change feed for the desktop mirror
  /sync/integrity   row counts + hashes, so a client can detect drift
  /api/...          reads for the phone, including server-computed graphs
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jobtracker.core import sync_policy, timeutils
from jobtracker.core.database import Database
from jobtracker.core.config import DB_PATH
from jobtracker.services.tracker_service import TrackerService

from . import auth, feed, ops

logger = logging.getLogger("jobtracker.server")

# One database connection and one service for the process. SQLite allows a single
# writer, and every write goes through /ops, so requests are serialised on this
# lock rather than racing each other.
_lock = threading.Lock()
_state: dict[str, Any] = {"db": None, "svc": None}


def build_state(db_path: Optional[str] = None) -> dict[str, Any]:
    database = Database(db_path or os.environ.get("JOBTRACKER_DB_PATH") or str(DB_PATH))
    feed.install(database.connection)
    # Recovery CLOSES sessions, so it runs once at startup and never per request.
    service = TrackerService(database, device_id="server", recover=True)
    logger.info("Server bound to %s", database.db_path)
    return {"db": database, "svc": service}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state.update(build_state())
    yield
    db = _state.get("db")
    if db is not None:
        db.connection.close()


app = FastAPI(title="JobTracker API", version="1.0.0", lifespan=lifespan)

# JSON of this shape compresses about ten to one, and the snapshot is the
# largest thing either client ever asks for. Small responses are left alone —
# compressing 80 bytes costs more than it saves.
app.add_middleware(GZipMiddleware, minimum_size=1024)


def svc() -> TrackerService:
    service = _state.get("svc")
    if service is None:
        raise HTTPException(status_code=503, detail="server not ready")
    return service


def require_device(authorization: str = Header(default="")) -> str:
    """Bearer-token auth. Returns the calling device's name."""
    prefix = "bearer "
    token = (
        authorization[len(prefix):].strip()
        if authorization.lower().startswith(prefix)
        else ""
    )
    device = auth.device_for_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return device


@app.exception_handler(ops.OpError)
async def _op_error_handler(_request, exc: ops.OpError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


# ── health ──────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    ready = _state.get("svc") is not None
    return {"status": "ok" if ready else "starting", "version": app.version}


# ── writes ──────────────────────────────────────────────────────────────


class OpRequest(BaseModel):
    op_id: str = Field(min_length=8, max_length=64)
    op: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    device_id: Optional[str] = None


class OpBatch(BaseModel):
    ops: list[OpRequest] = Field(min_length=1, max_length=200)


@app.post("/ops")
def apply_ops(batch: OpBatch, device: str = Depends(require_device)) -> dict:
    """Apply operations in the order given.

    A failure stops the batch: the client's outbox is ordered, and letting later
    operations jump a failed one is how a queue silently corrupts state. The
    response says exactly how far it got.
    """
    results: list[dict] = []
    with _lock:
        service = svc()
        for index, item in enumerate(batch.ops):
            try:
                result = ops.apply_op(
                    service, item.op, item.params, item.op_id, item.device_id or device
                )
            except ops.OpError as exc:
                return JSONResponse(
                    status_code=exc.status,
                    content={
                        "detail": exc.message,
                        "failed_op_id": item.op_id,
                        "failed_index": index,
                        "applied": results,
                        "seq": feed.current_seq(service.db.connection),
                    },
                )
            results.append({"op_id": item.op_id, "result": result})
        return {"applied": results, "seq": feed.current_seq(service.db.connection)}


@app.get("/ops/known")
def known_ops(device: str = Depends(require_device)) -> dict:
    return {"ops": ops.known_ops()}


# ── sync ────────────────────────────────────────────────────────────────


@app.get("/sync/pull")
def sync_pull(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=5000, ge=1, le=20000),
    device: str = Depends(require_device),
) -> dict:
    with _lock:
        service = svc()
        changes, cursor = feed.changes_since(service.db, since, limit)
        head = feed.current_seq(service.db.connection)
        # Shared settings are not rows, so they never appear in the change feed.
        # Sending them with every pull is what makes a day-start changed on the
        # phone actually reach the Mac; the set is tiny (currently one key).
        settings = {
            key: service.get_setting(key)
            for key in sorted(sync_policy.SYNCED_SETTING_KEYS)
            if service.get_setting(key)
        }
    return {
        "changes": changes,
        "seq": cursor,
        "head": head,
        "more": cursor < head,
        "settings": settings,
    }


@app.get("/sync/integrity")
def sync_integrity(
    deep: bool = Query(default=False),
    device: str = Depends(require_device),
) -> dict:
    """Proof that a client is in step. Row counts always; hashes on request.

    A mismatch means the mirror drifted; the client's answer is to throw the
    mirror away and re-download, which is always safe because the mirror is never
    authoritative.

    Counts alone catch anything added or lost, cost one COUNT(*) per table, and
    so can be checked on every sync. Hashing every uid catches the rarer case of
    the right number of wrong rows, and is reserved for an occasional deep check.
    """
    with _lock:
        service = svc()
        cur = service.db.connection.cursor()
        tables: dict[str, dict] = {}
        for table in sync_policy.SYNCED_TABLES:
            if deep:
                cur.execute(f"SELECT uid FROM {table} ORDER BY uid")
                uids = [row["uid"] or "" for row in cur.fetchall()]
                tables[sync_policy.API_NAMES[table]] = {
                    "count": len(uids),
                    "hash": hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest(),
                }
            else:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                tables[sync_policy.API_NAMES[table]] = {
                    "count": int(cur.fetchone()["n"])
                }
        head = feed.current_seq(service.db.connection)
    # The wall clock rides along so a client can notice the server drifting
    # into another timezone: times are stored naive, so a mismatch silently
    # files work at the wrong hour.
    return {
        "tables": tables,
        "head": head,
        "deep": deep,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


# ── reads ───────────────────────────────────────────────────────────────


def _raw_rows(codec, table: str, where: str = "", params: tuple = ()) -> list[dict]:
    """Rows in wire form. The codec resolves foreign keys from a preloaded map,
    so this is one query for the rows plus one per referenced table — not one
    per row, which is what it used to be."""
    cur = codec.db.connection.cursor()
    cur.execute(f"SELECT * FROM {table} {where}", params)
    return [codec.to_wire(table, dict(r)) for r in cur.fetchall()]


@app.get("/api/snapshot")
def api_snapshot(device: str = Depends(require_device)) -> dict:
    """Everything a fresh client needs, in one call.

    This is the "throw the mirror away and start over" path, and also what the
    phone loads on a cold start.
    """
    with _lock:
        service = svc()
        db = service.db
        codec = sync_policy.WireCodec(db)
        payload = {
            sync_policy.API_NAMES[table]: _raw_rows(codec, table)
            for table in sync_policy.SYNCED_TABLES
        }
        payload["settings"] = {
            key: db.get_setting(key)
            for key in sorted(sync_policy.SYNCED_SETTING_KEYS)
            if db.get_setting(key)
        }
        payload["head"] = feed.current_seq(db.connection)
        active = service.active_session
        payload["active_session"] = (
            codec.to_wire(
                "sessions",
                dict(
                    db.connection.execute(
                        "SELECT * FROM sessions WHERE id = ?", (active.id,)
                    ).fetchone()
                ),
            )
            if active is not None and active.id is not None
            else None
        )
    return payload


@app.get("/api/active")
def api_active(device: str = Depends(require_device)) -> dict:
    """The running timer, as any device sees it.

    Re-read from the database rather than trusting in-memory state, so a timer
    started by another client is visible immediately.
    """
    with _lock:
        service = svc()
        service._adopt_open_session()
        active = service.active_session
        if active is None or active.id is None:
            return {"active": None}
        row = service.db.connection.execute(
            "SELECT * FROM sessions WHERE id = ?", (active.id,)
        ).fetchone()
        subject_uid = service.db.uid_for_id("tasks", active.subject_id)
        elapsed = timeutils.duration_seconds(
            timeutils.parse_iso(active.start_time), datetime.now()
        )
    return {
        "active": sync_policy.to_wire(service.db, "sessions", dict(row)),
        "subject_uid": subject_uid,
        "elapsed_seconds": elapsed,
    }


@app.get("/api/context")
def api_context(device: str = Depends(require_device)) -> dict:
    """Today, as the server reckons it.

    The logical day starts at 03:00 by default, so "today" is not simply the
    calendar date. The phone asks rather than working it out, because a second
    implementation of that rule is a second thing that can disagree with the Mac.
    """
    with _lock:
        service = svc()
        day_start = service.get_day_start()
        today = timeutils.logical_day(datetime.now(), day_start)
        # `head` lets a client ask "has anything changed?" for a few dozen bytes
        # instead of re-downloading a snapshot that is several hundred kilobytes.
        head = feed.current_seq(service.db.connection)
        service._adopt_open_session()
        active = service.active_session
        active_payload = None
        if active is not None and active.id is not None:
            active_payload = {
                "subject_uid": service.db.uid_for_id("tasks", active.subject_id),
                "start_time": active.start_time,
                "elapsed_seconds": timeutils.duration_seconds(
                    timeutils.parse_iso(active.start_time), datetime.now()
                ),
            }
        return {
            "today": today.isoformat(),
            "day_start": timeutils.day_start_to_str(day_start),
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "head": head,
            "active": active_payload,
        }


@app.get("/api/graphs/breakdown")
def api_breakdown(
    grouping: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(default=14, ge=1, le=3660),
    device: str = Depends(require_device),
) -> dict:
    """Server-computed totals — the phone draws, it never recalculates.

    That is what stops the phone's numbers from disagreeing with the Mac's.
    """
    with _lock:
        service = svc()
        codec = sync_policy.WireCodec(service.db)
        buckets = service.get_subject_breakdown(grouping=grouping, days=days)
        out_buckets = []
        for bucket in buckets:
            out_buckets.append(
                {
                    "date": bucket["date"],
                    "total_seconds": bucket["total_seconds"],
                    "intensity_seconds": bucket["intensity_seconds"],
                    "segments": [
                        {
                            "subject_uid": codec.uid_for_id("tasks", seg["subject_id"]),
                            "subject_name": seg["subject_name"],
                            "color": seg["color"],
                            "seconds": seg["seconds"],
                            "start_time": seg.get("start_time"),
                            "end_time": seg.get("end_time"),
                        }
                        for seg in bucket["segments"]
                    ],
                }
            )
    return {"grouping": grouping, "buckets": out_buckets}


@app.get("/api/graphs/agenda")
def api_agenda(
    days: int = Query(default=7, ge=1, le=60),
    device: str = Depends(require_device),
) -> dict:
    """The agenda timeline: sessions placed at their clock position.

    ``start_h``/``end_h`` are agenda hours within the logical day — after-midnight
    work maps to 24..27 so it renders at the bottom of the day it belongs to
    rather than padding the top of the next one. The phone only draws these.
    """
    with _lock:
        service = svc()
        day_start = service.get_day_start()
        end_day = service.graph_end_day(day_start)
        start_day = timeutils.logical_day(datetime.now(), day_start) - timedelta(
            days=days - 1
        )
        codec = sync_policy.WireCodec(service.db)
        day_keys, sessions = service.get_agenda_data(start_day, end_day, day_start)
        out = [
            {
                "uid": codec.uid_for_id("sessions", s.get("session_id")),
                "day": s["day"],
                "subject_uid": codec.uid_for_id("tasks", s.get("subject_id")),
                "subject_name": s["subject_name"],
                "color": s["color"],
                "start_h": s["start_h"],
                "end_h": s["end_h"],
                "duration_seconds": s["duration_seconds"],
            }
            for s in sessions
        ]
    return {
        "days": day_keys,
        "sessions": out,
        "day_start_hour": day_start.hour + day_start.minute / 60.0,
    }


def _mount_web() -> None:
    """Serve the phone app from this same server.

    Mounted last, after every API route, because a mount at "/" would otherwise
    swallow them. The app is static files only — no build step, nothing to
    install — so deploying it is the same rsync that ships the server.
    """
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if not web_dir.is_dir():
        logger.warning("No web/ directory next to the server; phone app not served")
        return
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    logger.info("Serving the phone app from %s", web_dir)


@app.get("/api/sessions/day/{day}")
def api_sessions_for_day(day: str, device: str = Depends(require_device)) -> dict:
    try:
        logical = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    with _lock:
        service = svc()
        codec = sync_policy.WireCodec(service.db)
        rows = service.get_sessions_for_logical_day(logical)
        out = []
        for row in rows:
            out.append(
                {
                    # None for the live session, which has no persisted row yet —
                    # the desktop treats that the same way (never editable).
                    "uid": codec.uid_for_id("sessions", row.get("session_id")),
                    "subject_uid": codec.uid_for_id("tasks", row.get("subject_id")),
                    "subject_name": row.get("subject_name"),
                    "color": row.get("color"),
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "duration_seconds": row.get("duration_seconds"),
                }
            )
    return {"day": day, "sessions": out}


# Registered last on purpose: a mount at "/" shadows anything added after it.
_mount_web()
