# JobTracker

A self-hosted tracker for time, goals and body weight, with a native macOS app
and two installable iPhone web apps, **Weight** and **JobTracker**. It works offline and can synchronize through
your own authenticated server. Each deployment is intended for one person.

No advertising, analytics, third-party accounts or remote logging. Your history
stays on your devices and server; this public repository contains application
source and synthetic test data. See the [privacy policy](docs/privacy.md).

## What it does

- **Track time:** colour-coded subjects, start/stop/switch controls, manual
  entries, session editing, archive and crash-safe recovery.
- **Manage goals:** descriptions, milestones, recurring templates and weekly
  focus. Goals stay separate from timed subjects.
- **Record weight:** whole-kilogram and decimal tiles, calendar dates, historical
  entries, editing and deletion. The phone's ↻ button clears a selection.
- **Explore history:** stacked time bars, agenda timelines and weight trends.
  Phone weight plots offer chart-only fullscreen, custom ranges and PNG/CSV
  export. Thin year lines help orient long histories.
- **Work offline:** local SQLite on the Mac and a durable write queue on the
  phone. Reconnecting sends queued changes in order without duplicating saves.
- **Keep control:** local backups, explicit restore, per-device preferences,
  themes, reduced motion, keyboard access and visible sync status.

## Run on a Mac

Requires macOS 12+ and Python 3.12+. Clone the repository and run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

The development database lives in ignored `data/`. A packaged app stores its
history in `~/Library/Application Support/JobTracker/`. Running a source checkout
uses a different database from the installed app. Both work without a server.

To build a signed app outside iCloud:

```sh
python3 scripts/dev.py build --python /path/to/python
```

The [build/install guide](docs/runbooks/build-install.md) explains dependencies,
safe installation, queued updates and backups. Active sessions and pending
writes block app replacement; the installer waits for safe idle.

## Use on an iPhone

Deploy to your own server using the [deployment guide](docs/runbooks/deploy.md),
then open `/phone/` on that HTTPS server in Safari for installation instructions.
Install **Weight** from `/phone/weight/` and **JobTracker** from
`/phone/jobtracker/` using **Share → Add to Home Screen**. Sign in to each app
with its device token. Weight opens directly to weighing; JobTracker opens to
Subjects and has Goals, Sessions and Graphs. Backgrounding keeps each app's
current tab and unfinished form while iOS retains its page.

**Already have J? Keep it.** It becomes Weight using its existing connection and
pending queue. iOS may retain the J name/icon; follow the
[installation and migration guide](docs/guides/iphone-apps.md) before replacing
anything. JobTracker keeps the selected Cutout J, with a matching W for Weight.
The [legacy icon guide](docs/guides/iphone-icons.md) explains older choices.
The complete browser app remains at `/`, and the Mac app keeps every area.

The server is a private, single-user API. It shares the Mac's service rules and
accepts writes through authenticated, retry-safe operations. Device tokens do
not create independent user accounts. Keep it on the private HTTPS network
configured in the deployment guide.

## Dates, imports and backups

Time tracking uses a logical day beginning at **03:00** by default; Settings can
change the boundary. Weight uses ordinary calendar dates. See the
[time model](docs/architecture/time-model.md) for exact attribution rules.

Settings can export a full backup for restoring history, plus CSV files for
reading elsewhere. A clean Mac quit writes rotating JSON backups beside the
database. Keep backups private; see the [recovery guide](docs/runbooks/incidents.md)
before repairing or restoring a live database.

An optional [weight importer](docs/runbooks/weight-import.md) supports legacy
CSV/spreadsheet histories. Imports and exports are local user data and must never
be committed to this repository.

## Develop and contribute

Start with the [documentation index](docs/index.md) and compact
[engineering rules](docs/engineering-rules.md). Find owners without reading the
whole repository:

```sh
python3 scripts/dev.py context
python3 scripts/dev.py context weight graph
python3 scripts/dev.py context web/styles/shell.css
```

Use the returned feature ID for exact owners, contracts and focused checks.
[Generated feature](docs/generated/feature-index.md),
[operation](docs/generated/operations.md) and
[interface](docs/generated/interfaces.md) maps are checked against source.

Install test dependencies and enable the privacy guard:

```sh
python -m pip install -r requirements-dev.txt
git config core.hooksPath .githooks
python3 scripts/dev.py check
python3 scripts/dev.py test --python /path/to/python
python3 scripts/dev.py mobile
python3 scripts/dev.py browser
```

The [testing guide](docs/runbooks/testing.md) covers isolated databases,
offscreen Qt and optional Playwright tooling. Tests must not use real history,
credentials or deployment values. Store personal notes in ignored `.local/`.

**Agent commit policy:** after appropriate verification, automatically create
small local commits grouped by feature, fix or refactor, including the related
tests and documentation. Stage only relevant files or hunks and preserve other
uncommitted work. Never push, open a PR, amend or rewrite history unless explicitly
requested. [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) share this policy.

## License

MIT.
