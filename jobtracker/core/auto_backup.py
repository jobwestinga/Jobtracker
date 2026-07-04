"""
Automatic JSON safety backups.

On every clean quit the full authoritative export (subjects, sessions, goals,
milestones, templates, settings — the same payload as a manual JSON backup) is
written to ``BACKUPS_DIR`` and old copies are pruned so only the newest
``DEFAULT_KEEP`` remain. Purely local file writes; restoring goes through the
normal JSON import.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("jobtracker")

BACKUP_PREFIX = "autobackup_"
DEFAULT_KEEP = 10


def write_auto_backup(
    data: dict,
    backups_dir: Path,
    keep: int = DEFAULT_KEEP,
    now: Optional[datetime] = None,
) -> Path:
    """Write one timestamped backup file and prune older ones. Returns its path."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = backups_dir / f"{BACKUP_PREFIX}{stamp}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    prune_old_backups(backups_dir, keep)
    logger.info("Auto-backup written: %s", path.name)
    return path


def prune_old_backups(backups_dir: Path, keep: int = DEFAULT_KEEP) -> List[Path]:
    """Delete all but the newest ``keep`` auto-backups. Returns deleted paths.

    Timestamped names sort chronologically, so plain name order is enough.
    Only files matching the auto-backup pattern are ever touched.
    """
    files = sorted(backups_dir.glob(f"{BACKUP_PREFIX}*.json"))
    excess = files[:-keep] if keep > 0 else files
    removed: List[Path] = []
    for stale in excess:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError:
            logger.exception("Could not delete old auto-backup %s", stale)
    return removed
