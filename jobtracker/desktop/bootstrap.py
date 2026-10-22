"""Resolve the engine's store explicitly, before the database module is imported.

``core/config.py`` picks a path from ``sys.frozen`` and the eager ``db`` singleton
opens it on import. A helper frozen inside a different bundle must not fall into
the development store by accident, and a prototype must never default to either
real store, so the engine requires an explicit launch mode and exports the
resolved path through ``JOBTRACKER_DB_PATH`` before any database import.

This module deliberately imports nothing from ``jobtracker``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "JobTracker"
MODES = ("production", "development", "isolated")


@dataclass(frozen=True)
class StoreSelection:
    mode: str
    db_path: Path

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent


def production_db_path() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME / "jobtracker.db"


def development_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "jobtracker.db"


def canonical(path: Path) -> Path:
    """Absolute path with symlinks resolved, even when the file does not exist."""
    return Path(os.path.realpath(Path(path).expanduser()))


def real_store_paths() -> set[Path]:
    paths = {canonical(production_db_path())}
    if not getattr(sys, "frozen", False):
        paths.add(canonical(development_db_path()))
    return paths


def resolve_store(mode: str, db: str | os.PathLike | None = None) -> StoreSelection:
    """Return the one store this engine may own, or raise ``ValueError``."""
    if mode not in MODES:
        raise ValueError(f"Unknown engine mode {mode!r}; expected one of {', '.join(MODES)}")
    if mode == "production":
        if db is not None:
            raise ValueError("Production mode uses the installed store; do not pass --db")
        return StoreSelection(mode, canonical(production_db_path()))
    if mode == "development":
        if getattr(sys, "frozen", False):
            raise ValueError("Development mode is only available from a source checkout")
        if db is not None:
            raise ValueError("Development mode uses the checkout store; do not pass --db")
        return StoreSelection(mode, canonical(development_db_path()))
    if db is None:
        raise ValueError("Isolated mode requires an explicit --db path")
    path = canonical(Path(db))
    if path in real_store_paths():
        raise ValueError("Isolated mode must not open the production or development store")
    if path.suffix != ".db":
        raise ValueError("Isolated store paths must end in .db")
    return StoreSelection(mode, path)


def apply_environment(selection: StoreSelection) -> None:
    """Point config/database at the selection; refuse if they already differ."""
    already = sys.modules.get("jobtracker.core.config")
    if already is not None and canonical(Path(already.DB_PATH)) != selection.db_path:
        raise RuntimeError("The database module was imported before the engine store was selected")
    selection.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JOBTRACKER_DB_PATH"] = str(selection.db_path)
