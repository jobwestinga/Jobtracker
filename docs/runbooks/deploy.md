# Deploy the own-server API and phone app

Deployment writes to the user's own server. Perform it within the user's
authorized task scope; a local test run is not a deployment. Routine updates
preserve existing system files, private network exposure, other hosted services
and their proxy/DNS configuration.

## Prepare

Run the [source and test checks](testing.md). Changes to server routes, the
operation registry or handlers must reach the server before clients rely on
them. For phone assets, bump the service-worker shell version and verify all
transitive imports/styles are included in `SHELL`:

```sh
python scripts/dev.py check
```

Use the ignored `deploy/server.env`. For a new environment, copy
[server.env.example](../../deploy/server.env.example) and fill it locally.
Hostnames, logins, private addresses, token values and rendered units must never
enter tracked documentation or source. Each device has its own token; server
token files contain hashes and have restrictive permissions.

The service timezone must match the devices' local clocks because timestamps
are naive local wall time. Keep uvicorn on loopback and the configured HTTPS
endpoint on the private network. Preserve the selected HTTPS port when another
hosted service already uses the usual port. Tailscale Serve needs the configured
Host header; a bare IP request does not select the route.

## Routine update

```sh
python scripts/dev.py deploy
```

The direct equivalent is `./deploy/install.sh`. It takes an online SQLite backup
before shipping source and assets, verifies the existing service unit and
restarts only the matching supervised API. Committed WAL data is included in
the backup. An interrupted operation remains atomic and retryable by its
original operation ID; server restart must never run automatic timer recovery.

If the existing unit differs, the routine deployment fails instead of changing
root-owned files. `./deploy/install.sh --provision` is a separate provisioning
action requiring configured sudo access. Resolve the stated access/configuration
need; do not broaden sudo permissions or change unrelated services to work
around it. New server dependencies also require updating that server's Python
environment from `requirements-server.txt`; shipping files alone does not do so.

## Verify the running service

The deployment verifies supervised health. Then check the configured HTTPS
`/health`, an authenticated context/weight read and one relevant client refresh.
Keep tokens out of command history and logs. Verify that the running operation
registry includes any operation emitted by the changed clients; source tests
only check an in-process server.

If testing on the server itself with private-network DNS intentionally disabled,
use values from its configuration:

```sh
curl --resolve "$JT_TAILNET_HOST:$JT_HTTPS_PORT:$JT_TAILNET_IP" \
  "https://$JT_TAILNET_HOST:$JT_HTTPS_PORT/health"
```

Preserve `--accept-dns=false`; do not change DNS for the user's other services.
Scheduled backups should continue to use the online backup API. Check service
logs locally if health or authentication fails; redact deployment details before
sharing a report.

## Refresh the phone

Let pending changes finish syncing, then close and reopen the phone app, or
reload its page while connected to the private network. The network-first shell
updates assets; API data is never cached by the service worker. A fresh launch
opens its profile's initial view (Weight entry or JobTracker Subjects); foreground
resume preserves the active tab and draft. The existing J installation retains
its connection and becomes Weight. See [the migration guide](../guides/iphone-apps.md)
before replacing any icon. The complete browser remains at `/`.

Confirm a weight value can be selected in both tile rows and saved, the plot
opens fullscreen in either orientation, and tracking history refreshes. Do not
clear website data, local storage or credentials as an update step: that can
remove durable pending operations. Use the [incident runbook](incidents.md) for
an old shell, blocked queue or failed deployment.
