"""
Central configuration for JobTracker.

Path resolution handles both:
  • Development mode  (python main.py)
  • Frozen mode       (PyInstaller .app bundle)

Design tokens are now provided dynamically by themes.py.
This module only handles paths and app identity.
"""

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
if IS_FROZEN:
    _app_support = Path.home() / "Library" / "Application Support" / APP_NAME
else:
    _app_support = BUNDLE_DIR / "data"

DATA_DIR = _app_support
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "jobtracker.db"

# ── Static radius tokens (used everywhere, never change) ────────────────────
RADIUS_SM = "6px"
RADIUS_MD = "10px"
RADIUS_LG = "14px"
