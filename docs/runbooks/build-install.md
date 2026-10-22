# Build and install on macOS

Use these tools for an authorized app update. They stage the build outside iCloud,
verify the signed bundle and preserve the prior app and database backups. Do not
delete `/Applications/JobTracker.app`, copy a replacement over a running bundle,
or force-kill the application to make installation possible.

## Build a reviewable bundle

Run the [required checks](testing.md) first. Select a Python interpreter containing
PySide6 and PyInstaller:

```sh
python scripts/dev.py build --python /path/to/python
```

The developer command defaults to **build only**. It stages under
`~/Library/Caches/JobTracker/builds/` and reports the signed staged app path.
It does not replace the installed application. `--setup` explicitly creates a
build environment and installs its dependencies. Build-only mode also retains a
repository `dist/` copy for legacy compatibility; `--keep-dist` retains that copy
when requesting installation too. A running `dist/` bundle is preserved. Use the reported outside-iCloud staged bundle for installation;
copying a bundle into iCloud can change extended attributes after signing.

The direct build-only equivalent is:

```sh
python scripts/build_macos.py --python /path/to/python --no-install
```

The direct script and legacy `build_macos.sh` retain their install-by-default
environment compatibility. Always pass `--no-install` when staging only.
`JOBTRACKER_PYTHON` selects the interpreter; `INSTALL_TO_APPLICATIONS` and
`KEEP_DIST_APP` remain available for the legacy wrapper. Do not use ad hoc
PyInstaller/copy/sign commands as an installation shortcut.

## Check and install

Read the development/installed database and process summary without opening app
services or running migrations:

```sh
python scripts/dev.py status
```

For the installer's full fail-closed guard report, use
`python scripts/install_macos.py --status`. The developer summary does not
replace that guard. By default the installer requires both the packaged database
under
`~/Library/Application Support/JobTracker/` and the development database under
`data/`. It opens existing databases read-only. Any open session, including one
owned by another device, or any pending outbox blocks replacement. A missing or
unreadable required database is an error, not permission to proceed.

Build and request a guarded installation in one command:

```sh
python scripts/dev.py build --python /path/to/python --install
```

Or install an already reviewed staged bundle:

```sh
python scripts/install_macos.py /path/to/staged/JobTracker.app \
  --python /path/to/python
```

If busy, the default queues a self-contained installer and bundle under
`~/JobTracker-updates/pending/`. Its LaunchAgent waits for stable idle, then asks
the old app to quit normally, rechecks safety and installs. It never forces a
quit. The pending folder contains `config.json` and `status.json`; the private
`pending.json` pointer identifies the current request. To cancel an intended
queued update, use `python scripts/install_macos.py --cancel-pending` rather
than deleting these records. `--no-queue` refuses a busy install immediately;
`--no-relaunch` keeps the app closed after installation. Do not alter sessions or clear queued writes
to bypass the guard.

For an intentionally different installation, use `--project`, `--application`
and repeat `--database` for the complete set of affected databases; explicit
database options replace the default list. `--allow-missing-databases` is only
for a deliberate fresh installation whose data does not yet exist. It is not a
recovery shortcut for an unreadable or unexpectedly missing database.

## What is preserved

Before replacement, the installer takes SQLite online backups of every existing
required database and saves the previous signed application under
`~/JobTracker-updates/backups/`. `sources.json` identifies backup sources.
Replacement uses sibling renames; an installation failure restores application
code, **never** an older database. Preserve queued status files and backups when
investigating failures; use the [incident runbook](incidents.md).

If installation remains queued, finish or explicitly resolve your own timer and
let pending changes sync normally. Inspect status and report the blocking reason.
Reuse authorization already given for the update; do not add a second approval
step merely because the safe tool waits for idle.

## Native Mac app (A4)

The native frontend builds outside iCloud with SwiftPM and a frozen, Qt-free
engine; the Command Line Tools are sufficient. It never installs:

```sh
python scripts/dev.py native --python /path/to/python        # build + native checks
python scripts/dev.py build --native --python /path/to/python  # signed pilot bundle
```

The default `pilot` identity is a separate app, `com.jobtracker.native-pilot`
("JobTracker Native Pilot"), on its own store under
`~/Library/Application Support/JobTracker Native Pilot/`. It cannot replace
`/Applications/JobTracker.app` (the installer requires `com.jobtracker.app`).
`--identity production` keeps the established identity and store and exists
only for the guarded cutover described in the
[A4 guide](../guides/a4-native-mac-overhaul.md#p17--pilot-switch-defaults-and-maintain-the-result).

The engine is frozen with PyInstaller into `Contents/Resources/engine/`; every
nested Mach-O is signed before the app, and `codesign --verify --deep
--strict` must pass. The build smoke-tests the frozen engine from a neutral
folder on a disposable store and reports architectures and size.

Installer guards treat a running native UI or a settling engine helper as a
running app, and unresolved native requests (`native-pending-requests/` beside
a store) as pending work, exactly like an open session or outbox.
