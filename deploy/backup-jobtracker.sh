#!/usr/bin/env bash
# Nightly server-side backup, kept for 30 days.
#
# Uses SQLite's online backup API through the venv's Python rather than the
# sqlite3 CLI: one less package to install, and it is explicitly safe to run
# while the API is serving requests (no torn copy, no locking the writer out).
#
# JT_ROOT is substituted by deploy/install.sh from deploy/server.env.
set -euo pipefail

ROOT="${JT_ROOT:-__JT_ROOT__}"
DB="$ROOT/data/jobtracker.db"
OUT="$ROOT/backups"
PYTHON="$ROOT/venv/bin/python"

mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="$OUT/jobtracker-$STAMP.db"

"$PYTHON" - "$DB" "$DEST" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
target = sqlite3.connect(dest)
with target:
    source.backup(target)
target.close()
source.close()
PY

gzip -f "$DEST"
find "$OUT" -name 'jobtracker-*.db.gz' -mtime +30 -delete
