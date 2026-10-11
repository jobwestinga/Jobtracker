#!/usr/bin/env bash
# Deploy the JobTracker API to your own server.
#
#   cp deploy/server.env.example deploy/server.env   # fill in your values
#   ./deploy/install.sh                              # ship code + restart
#   ./deploy/install.sh --provision                  # also install system files
#
# Run from the repository root. Idempotent: safe to re-run for every deploy.
# Everything machine-specific comes from deploy/server.env, which is gitignored
# because this repository is public.
set -euo pipefail

JT_PROVISION=0
case "${1:-}" in
    "") ;;
    --provision) JT_PROVISION=1 ;;
    *) echo "Usage: $0 [--provision]" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/server.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE — copy server.env.example and fill it in." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${JT_SSH_USER:?}" "${JT_SSH_HOST:?}" "${JT_ROOT:?}" "${JT_API_PORT:?}"
SSH_TARGET="$JT_SSH_USER@$JT_SSH_HOST"
SSH_OPTS=${JT_SSH_OPTS:-}

echo "==> Backing up the live database before any schema changes"
printf -v JT_QUOTED_ROOT '%q' "$JT_ROOT"
# The online backup API includes committed WAL data and never stops a timer.
# shellcheck disable=SC2086
ssh $SSH_OPTS "$SSH_TARGET" "python3 - $JT_QUOTED_ROOT" <<'PY'
from pathlib import Path
from datetime import datetime
import sqlite3
import sys
root = Path(sys.argv[1])
source = root / "data/jobtracker.db"
if source.exists():
    folder = root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("pre-upgrade-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    target.chmod(0o600)
    print("Live database backup saved")
PY

echo "==> Shipping code to $SSH_TARGET:$JT_ROOT/app"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$SSH_TARGET" "mkdir -p '$JT_ROOT/app' '$JT_ROOT/data' '$JT_ROOT/secrets' '$JT_ROOT/backups' && chmod 700 '$JT_ROOT/secrets'"
# shellcheck disable=SC2086
rsync -az --delete --exclude-from="$HERE/.rsync-exclude" \
    ${SSH_OPTS:+-e "ssh $SSH_OPTS"} \
    ./jobtracker ./server ./web ./requirements-base.txt ./requirements-server.txt ./deploy \
    "$SSH_TARGET:$JT_ROOT/app/"

echo "==> Rendering unit file and backup script"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -e "s|__JT_SSH_USER__|$JT_SSH_USER|g" \
    -e "s|__JT_ROOT__|$JT_ROOT|g" \
    -e "s|__JT_API_PORT__|$JT_API_PORT|g" \
    -e "s|__JT_TIMEZONE__|${JT_TIMEZONE:-UTC}|g" \
    "$HERE/jobtracker-api.service.template" > "$TMP/jobtracker-api.service"
sed -e "s|__JT_ROOT__|$JT_ROOT|g" "$HERE/backup-jobtracker.sh" > "$TMP/backup-jobtracker.sh"

# shellcheck disable=SC2086
scp $SSH_OPTS -q "$TMP/jobtracker-api.service" "$TMP/backup-jobtracker.sh" "$SSH_TARGET:/tmp/"

echo "==> Checking service configuration"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$SSH_TARGET" "
    set -e
    if [ '$JT_PROVISION' = 1 ]; then
        sudo -n install -m 0644 /tmp/jobtracker-api.service /etc/systemd/system/jobtracker-api.service
        sudo -n install -m 0755 /tmp/backup-jobtracker.sh /usr/local/bin/backup-jobtracker.sh
        sudo -n systemctl daemon-reload
        sudo -n systemctl enable --now jobtracker-api
    elif ! cmp -s /tmp/jobtracker-api.service /etc/systemd/system/jobtracker-api.service; then
        echo 'Service configuration differs. Provision the system files with --provision and the required sudo access.' >&2
        exit 1
    fi
    rm -f /tmp/jobtracker-api.service /tmp/backup-jobtracker.sh
"

echo "==> Restarting the existing API service"
# An unchanged service can restart under its existing supervisor without
# reinstalling root-owned files. The fallback targets only this account's API
# worker, never another service, and requires its on-failure/always policy.
# shellcheck disable=SC2086
ssh $SSH_OPTS "$SSH_TARGET" "python3 - $JT_QUOTED_ROOT '$JT_API_PORT'" <<'PY'
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import urllib.request

root, port = Path(sys.argv[1]), int(sys.argv[2])
restarted = subprocess.run(
    ['sudo', '-n', '/usr/bin/systemctl', 'restart', 'jobtracker-api'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
).returncode == 0
previous = None
if not restarted:
    props = dict(line.split('=', 1) for line in subprocess.check_output(
        ['systemctl', 'show', 'jobtracker-api', '-p', 'MainPID', '-p', 'Restart'],
        text=True,
    ).splitlines())
    previous = int(props['MainPID'])
    if previous < 2 or props['Restart'] not in ('on-failure', 'always'):
        raise SystemExit('The API needs an active restart supervisor or sudo restart access')
    process = Path('/proc') / str(previous)
    if process.stat().st_uid != os.getuid():
        raise SystemExit('The API process belongs to another account; restart requires sudo access')
    arguments = (process / 'cmdline').read_bytes().split(b'\0')
    if (os.fsencode(root / 'venv/bin/uvicorn') not in arguments
            or b'server.app:app' not in arguments):
        raise SystemExit('The supervised process does not match this JobTracker installation')
    # SIGUSR1 has a terminating default action and triggers the existing
    # supervisor. SQLite preserves committed WAL data and rolls back a partial
    # transaction; retryable client requests retain their operation identities.
    os.kill(previous, signal.SIGUSR1)

for attempt in range(60):
    time.sleep(0.5)
    current = int(subprocess.check_output(
        ['systemctl', 'show', 'jobtracker-api', '-p', 'MainPID', '--value'], text=True,
    ).strip())
    if not current or current == previous:
        continue
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
            health = response.read().decode()
        if subprocess.check_output(['systemctl', 'is-active', 'jobtracker-api'], text=True).strip() == 'active':
            print('active')
            print(health)
            break
    except (OSError, subprocess.CalledProcessError):
        pass
else:
    raise SystemExit('The API did not become healthy after restart')
PY

cat <<EOF

Deployed. Remaining one-time steps on the server, if you have not done them:

  # Python environment
  ssh $SSH_TARGET '$JT_ROOT/venv/bin/pip install -r $JT_ROOT/app/requirements-server.txt'

  # A token per device (printed once)
  ssh $SSH_TARGET 'cd $JT_ROOT/app && JOBTRACKER_TOKENS_PATH=$JT_ROOT/secrets/tokens.json \\
      $JT_ROOT/venv/bin/python -m server.tokens_cli add macbook'

  # Publish on the tailnet with a real certificate
  ssh $SSH_TARGET 'sudo tailscale serve --bg --https=${JT_HTTPS_PORT:-8443} http://127.0.0.1:$JT_API_PORT'

  # Nightly backup
  ssh $SSH_TARGET '(crontab -l 2>/dev/null | grep -v backup-jobtracker; \\
      echo "17 4 * * * /usr/local/bin/backup-jobtracker.sh >/dev/null 2>&1") | crontab -'
EOF
