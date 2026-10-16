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

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from jobtracker.core.database import Database
from jobtracker.core.config import DB_PATH
from jobtracker.services.tracker_service import TrackerService

from . import feed, ops
from .dependencies import require_device
from .runtime import _lock, _state, svc
from .routes.context import api_active, api_context, router as context_router
from .routes.graphs import api_agenda, api_breakdown, _plot_window, router as graphs_router
from .routes.ops import OpBatch, OpRequest, apply_ops, known_ops, router as ops_router
from .routes.sessions import api_sessions_for_day, router as sessions_router
from .routes.sync import api_snapshot, sync_integrity, sync_pull, _raw_rows, router as sync_router
from .routes.weight import api_weight, router as weight_router

logger = logging.getLogger("jobtracker.server")


def build_state(db_path: Optional[str] = None) -> dict[str, Any]:
    database = Database(db_path or os.environ.get("JOBTRACKER_DB_PATH") or str(DB_PATH))
    feed.install(database.connection)
    # The server is an authority, not a timer-owning UI. Restarts must preserve
    # every client's unfinished timer; desktop recovery belongs to that client.
    service = TrackerService(database, device_id="server", recover=False)
    service._adopt_open_session(include_other_devices=True)
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
# Compress large snapshots; small replies stay plain.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def private_data_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/", "/sync/")) or request.url.path == "/ops":
        response.headers["Cache-Control"] = "no-store"
        vary = [value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()]
        if "authorization" not in {value.lower() for value in vary}:
            vary.append("Authorization")
        response.headers["Vary"] = ", ".join(vary)
    elif request.url.path.endswith((".js", ".css", ".html", ".webmanifest", "/")):
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(ops.OpError)
async def _op_error_handler(_request, exc: ops.OpError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


@app.get("/health")
def health() -> dict:
    ready = _state.get("svc") is not None
    return {"status": "ok" if ready else "starting", "version": app.version}


# Keep explicit registration and endpoint compatibility aliases above. Routers
# must precede the final root mount, which would otherwise shadow the API.
for router in (ops_router, sync_router, context_router, graphs_router, sessions_router, weight_router):
    app.include_router(router)


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


# Registered last on purpose: a mount at "/" shadows anything added after it.
_mount_web()
