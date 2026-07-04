"""
Central configuration for JobTracker.

Path resolution handles both:
  • Development mode  (python main.py)
  • Frozen mode       (PyInstaller .app bundle)

Design tokens are now provided dynamically by themes.py.
This module only handles paths and app identity.
"""

import os
import sys
from pathlib import Path

# ── App identity ─────────────────────────────────────────────────────────────
APP_NAME = "JobTracker"

# ── Detect frozen (PyInstaller) vs development mode ──────────────────────────
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Assets (icons, images) — bundled read-only ───────────────────────────────
ASSETS_DIR = BUNDLE_DIR / "assets"
ICON_PATH  = ASSETS_DIR / "icon.icns"

# ── User data — writable, per-user ──────────────────────────────────────────
# Tests (and any tooling that must NOT touch real user data) can redirect the
# database by setting JOBTRACKER_DB_PATH before importing the package. This keeps
# the module-level Database() singleton off ~/Library/Application Support and off
# the dev ./data/jobtracker.db.
_env_db_path = os.environ.get("JOBTRACKER_DB_PATH")

if _env_db_path:
    DB_PATH = Path(_env_db_path).expanduser()
    DATA_DIR = DB_PATH.parent
else:
    if IS_FROZEN:
        _app_support = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        _app_support = BUNDLE_DIR / "data"
    DATA_DIR = _app_support
    DB_PATH = DATA_DIR / "jobtracker.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Auto-backup (JSON safety copies written on quit) ─────────────────────────
BACKUPS_DIR = DATA_DIR / "backups"
