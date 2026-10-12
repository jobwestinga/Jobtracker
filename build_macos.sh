#!/usr/bin/env bash
# Thin compatibility entry point. Dependencies are installed only with --setup.
set -euo pipefail

JT_BUILD_ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${JOBTRACKER_PYTHON:-}" ]; then
    JT_BUILD_PYTHON="$JOBTRACKER_PYTHON"
elif [ -x "$JT_BUILD_ROOT/venv/bin/python3" ]; then
    JT_BUILD_PYTHON="$JT_BUILD_ROOT/venv/bin/python3"
else
    JT_BUILD_PYTHON="$(command -v python3)"
fi
exec "$JT_BUILD_PYTHON" "$JT_BUILD_ROOT/scripts/build_macos.py" "$@"
