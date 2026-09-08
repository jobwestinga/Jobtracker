#!/usr/bin/env bash
# Deploy the JobTracker API to your own server.
#
#   cp deploy/server.env.example deploy/server.env   # fill in your values
#   ./deploy/install.sh                              # ship code + restart
#
# Run from the repository root. Idempotent: safe to re-run for every deploy.
# Everything machine-specific comes from deploy/server.env, which is gitignored
# because this repository is public.
set -euo pipefail

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

echo "==> Shipping code to $SSH_TARGET:$JT_ROOT/app"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$SSH_TARGET" "mkdir -p '$JT_ROOT/app' '$JT_ROOT/data' '$JT_ROOT/secrets' '$JT_ROOT/backups' && chmod 700 '$JT_ROOT/secrets'"
# shellcheck disable=SC2086
rsync -az --delete --exclude-from="$HERE/.rsync-exclude" \
    ${SSH_OPTS:+-e "ssh $SSH_OPTS"} \
    ./jobtracker ./server ./requirements-base.txt ./requirements-server.txt ./deploy \
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

echo "==> Installing service (sudo on the server)"
# shellcheck disable=SC2086
ssh $SSH_OPTS -t "$SSH_TARGET" "
    set -e
    sudo install -m 0644 /tmp/jobtracker-api.service /etc/systemd/system/jobtracker-api.service
    sudo install -m 0755 /tmp/backup-jobtracker.sh /usr/local/bin/backup-jobtracker.sh
    rm -f /tmp/jobtracker-api.service /tmp/backup-jobtracker.sh
    sudo systemctl daemon-reload
    sudo systemctl enable --now jobtracker-api
    sudo systemctl restart jobtracker-api
    sleep 2
    systemctl is-active jobtracker-api
    curl -sf http://127.0.0.1:$JT_API_PORT/health && echo
"

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
