# Privacy and public repository policy

JobTracker is open-source software for a self-hosted, single-user deployment.
The source repository is public; each person's history and credentials stay on
their own devices and server. A public repository does not expose the running
app's database or give access to its authenticated API.

## Keep local

- SQLite databases and sidecars, backups, exports and logs.
- Weight history, imported spreadsheets, scale CSVs and health-data exports.
- Access tokens, private keys, certificates and `.env` files.
- Deployment hostnames, network addresses, logins and populated server settings.
- Personal screenshots, release notes and temporary planning documents.

Use the ignored `.local/` folder for project notes and temporary review outputs.
Keep deployment settings in ignored `deploy/server.env`; use
[server.env.example](../deploy/server.env.example) as the public template.
Never put credentials into screenshots, issue descriptions, commit messages or
example commands. Public test fixtures must be synthetic and described as such.
Developer architecture, source maps and runbooks remain public because they help
others maintain and self-host the app. Remove completed plans instead of keeping
dated personal release diaries alongside current documentation.

## Before committing

```sh
git config core.hooksPath .githooks
python3 scripts/dev.py check
```

The [local pre-commit hook](../.githooks/pre-commit) checks the exact staged
content, including ignored files added with `git add -f`.
[check_privacy.py](../scripts/check_privacy.py) also runs in the standard checks
and CI. It rejects private file types, serialized weight history, personal home
paths, private network details, embedded PNG metadata and recognizable secret formats. Reports show
locations and categories, never matching values. This conservative guard cannot
identify every secret or prove that an arbitrary reading is synthetic; review
what is staged as well. Git ignore rules alone do not protect tracked files.

The app's tests use isolated databases and mock browser data. The normal build
stages application source and artwork without copying the user's database.
Follow the [deployment](runbooks/deploy.md) and [build/install](runbooks/build-install.md)
runbooks; keep pending writes and active sessions intact.

## Existing history

Deleting a tracked file removes it from the next version, not earlier commits.
Cleanup commits do not erase earlier public copies, forks or downloads. If an
actual credential was published, revoke/rotate it before coordinating a history
cleanup. Do not rewrite history or force-push as an automatic housekeeping step.

## Network and deployment limits

Application data goes only to the configured own-server endpoint. There is no
analytics, advertising, external account or remote logging. Device credentials
authorize access to the single-user history; they do not create independent
user accounts. Keep the API behind the configured private HTTPS network and
never expose the SQLite database through the static `web/` directory.
