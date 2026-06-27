"""
Minimal logging setup for JobTracker.

Local desktop app only — there is NO remote logging or telemetry. Logs go to a
rotating file under the app's data directory and to stderr. The log file is
useful for debugging database errors, import/export failures, active-session
recovery, and otherwise-silent exceptions in non-critical UI logic.

Modules obtain their logger with ``logging.getLogger("jobtracker")``. Until
:func:`setup_logging` is called (from ``main.py``), those loggers simply have no
handlers, which keeps imports (and the test suite) side-effect free.
"""

from __future__ import annotations

import logging
import logging.handlers
from typing import Optional

from .config import DATA_DIR

LOGGER_NAME = "jobtracker"

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the ``jobtracker`` logger once. Safe to call repeatedly."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler under the (already writable) data directory.
    try:
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "jobtracker.log",
            maxBytes=512_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # If the log file can't be created (e.g. read-only volume) we still want
        # the app to run — fall back to stderr only.
        pass

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    _configured = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the app logger (or a child of it)."""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)
