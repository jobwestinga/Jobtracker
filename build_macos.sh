#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# build_macos.sh — Build JobTracker as a macOS .app bundle
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔══════════════════════════════════════════════╗"
echo "║  Building JobTracker.app for macOS           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Ensure virtual environment ────────────────────────────
if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment …"
    python3 -m venv venv
fi

echo "→ Activating virtual environment …"
source venv/bin/activate

# ── 2. Install / update dependencies ────────────────────────
echo "→ Installing dependencies …"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# ── 3. Ensure assets/ exists ─────────────────────────────────
mkdir -p assets

# ── 4. Build/update macOS icon from PNG if available ─────────
ICON_PNG=""
if [ -f "assets/icon.png" ]; then
    ICON_PNG="assets/icon.png"
elif [ -f "JobTracker.png" ]; then
    ICON_PNG="JobTracker.png"
fi

if [ -n "$ICON_PNG" ]; then
    echo "→ Regenerating assets/icon.icns from $ICON_PNG …"
    ICON_TMP_DIR="$(mktemp -d)"
    ICON_BASE="$ICON_PNG"

    # Finder/Spotlight can show a gray matte around icons when alpha is preserved.
    # For the .icns bundle icon, flatten alpha onto a dark tone to avoid halo artifacts.
    if sips -g hasAlpha "$ICON_PNG" | grep -q "hasAlpha: yes"; then
        if ICON_SRC="$ICON_PNG" ICON_OUT="$ICON_TMP_DIR/base.png" python3 - <<'PY'
from pathlib import Path
import os
import sys

from PySide6.QtGui import QColor, QImage, QPainter

src = Path(os.environ["ICON_SRC"])
out = Path(os.environ["ICON_OUT"])
img = QImage(str(src))
if img.isNull():
    sys.exit(1)

base = QImage(img.size(), QImage.Format_ARGB32)
base.fill(QColor("#050B18"))

p = QPainter(base)
p.setRenderHint(QPainter.SmoothPixmapTransform, True)
p.drawImage(0, 0, img)
p.end()

flat = base.convertToFormat(QImage.Format_RGB32)
if not flat.save(str(out), "PNG"):
    sys.exit(1)
PY
        then
            ICON_BASE="$ICON_TMP_DIR/base.png"
        fi
    fi

    for size in 16 32 64 128 256 512 1024; do
        sips -z "$size" "$size" -s format png "$ICON_BASE" --out "$ICON_TMP_DIR/${size}.png" >/dev/null
    done

    if ! ICON_SRC_DIR="$ICON_TMP_DIR" python3 - <<'PY'
from pathlib import Path
import os
import struct
import sys

src = Path(os.environ["ICON_SRC_DIR"])
entries = [
    ("icp4", "16.png"),
    ("icp5", "32.png"),
    ("icp6", "64.png"),
    ("ic07", "128.png"),
    ("ic08", "256.png"),
    ("ic09", "512.png"),
    ("ic10", "1024.png"),
]

chunks = []
for icon_type, filename in entries:
    png_path = src / filename
    if not png_path.exists():
        print(f"missing {png_path}", file=sys.stderr)
        sys.exit(1)
    png_data = png_path.read_bytes()
    chunk_size = 8 + len(png_data)
    chunks.append(icon_type.encode("ascii") + struct.pack(">I", chunk_size) + png_data)

body = b"".join(chunks)
Path("assets/icon.icns").write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
print("wrote assets/icon.icns")
PY
    then
        echo "WARNING: failed to generate assets/icon.icns; keeping previous icon"
    fi
    rm -rf "$ICON_TMP_DIR"
fi

# Remove stale metadata that can break app signing + icon refresh.
xattr -cr assets/icon.icns 2>/dev/null || true
xattr -cr assets/icon.png 2>/dev/null || true

# ── 5. Clean previous builds ─────────────────────────────────
echo "→ Cleaning previous builds …"
rm -rf build dist

# ── 6. Run PyInstaller ────────────────────────────────────────
echo "→ Running PyInstaller …"
pyinstaller JobTracker.spec --noconfirm

# ── 7. Install to /Applications (optional, enabled by default) ─
INSTALL_TO_APPLICATIONS="${INSTALL_TO_APPLICATIONS:-1}"
KEEP_DIST_APP="${KEEP_DIST_APP:-0}"

if [ "$INSTALL_TO_APPLICATIONS" != "1" ] && [ "$KEEP_DIST_APP" = "0" ]; then
    KEEP_DIST_APP="1"
fi

xattr -cr dist/JobTracker.app 2>/dev/null || true

if [ "$INSTALL_TO_APPLICATIONS" = "1" ]; then
    echo "→ Installing app into /Applications …"
    rm -rf /Applications/JobTracker.app
    cp -R dist/JobTracker.app /Applications/
    xattr -cr /Applications/JobTracker.app 2>/dev/null || true
    touch /Applications
    touch /Applications/JobTracker.app
fi

if [ "$KEEP_DIST_APP" = "0" ]; then
    echo "→ Removing local build artifacts (KEEP_DIST_APP=0) …"
    rm -rf dist build
fi

echo ""
echo "──────────────────────────────────────────────────"
echo "✅  Build complete!"
echo ""
if [ "$INSTALL_TO_APPLICATIONS" = "1" ]; then
    echo "   Installed app: /Applications/JobTracker.app"
fi
if [ "$KEEP_DIST_APP" = "1" ]; then
    echo "   Dist app:      dist/JobTracker.app"
fi
echo ""
if [ "$INSTALL_TO_APPLICATIONS" = "1" ]; then
    echo "   To run:        open /Applications/JobTracker.app"
else
    echo "   To run:        open dist/JobTracker.app"
fi
echo ""
echo "   User data is stored in:"
echo "   ~/Library/Application Support/JobTracker/"
echo "──────────────────────────────────────────────────"
