# JobTracker

JobTracker tracks time, goals and daily body weight on a native macOS app and a
phone web app. The Mac keeps its data in SQLite and works offline. Optional sync
uses your own authenticated server; app data stays on machines you control.

For development, start at the [documentation index](docs/index.md) and
[engineering rules](docs/engineering-rules.md). Use
`python scripts/dev.py context weight.daily-entry` to find the relevant feature
owners, contracts and tests.

## Features

- **Daily weight on phone and Mac:** The Weight tab has seven whole-kilogram buttons centered on your latest reading and ten decimal buttons (.0–.9). Tap one in each row, then Save. On the phone, both rows span the available width and a durable save clears the selection. Entry controls adjust dates, tile size and contrast. Entries use calendar days, and saving again on the same day updates the manual entry. Offline saves enter a durable queue; previous readings can be edited or deleted.
- **Personal weight plots:** Choose 30/90 days, six months, a year, all history, or custom dates. The original LOESS trend uses a 4% span, with smoother options, a seven-day average, measurement visibility, historic readings, and an optional zero-based axis. Sparse historic readings use shape-preserving interpolation. The phone also exports plots as PNG and measurements as CSV.

- **Live Session Tracking:** Create color-tagged subjects and track your progress with a single click. Inline card actions (edit, manage sessions, archive) keep everything one tap away, and the active topic stays prominent while others dim. A live persistent dashboard tracks your active sessions. New subjects suggest colors that stay visually distinct from your existing ones.
- **Crash-safe recovery:** If the app or laptop dies mid-session, on reopen JobTracker asks how to handle the unfinished session — end it at the last time it was known active, end it now, or set a custom end time/length. It never silently counts hours you weren't working, and never deletes the session for you.
- **Detailed History & Visualizations:** Modify, backfill, or manage past sessions seamlessly—quick-add a preset (including 1h 30m) or combine custom duration buttons ending now, drop a session into a fixed time slot, duplicate one to today or the next day, or nudge it ±15m/±1h/±1 day without retyping any dates. The custom buttons add 4h, 2h, 1h, 30m, 15m, 10m and 5m; tap again to remove a chunk and check the total before saving. Exact time inputs remain available. Use stacked bar charts (grouped **daily, weekly, or monthly**) and compact agenda timelines. Pick a preset range (Weeks/Months/Year/All) or a custom date range. Hover a bar or agenda block for details, click a block to edit that exact session, or click a day to inspect everything on it.
- **Outcome-focused Goals:** Keep goals separate from timed subjects. Add a description and milestone checklist, track progress, complete a goal only after its milestones are done, and reopen completed goals at any time. Star the handful you're concentrating on with **Focus This Week** so they stand out in the list.
- **Recurring Goal Templates:** Daily, weekly, or monthly templates generate ordinary goals at the top of the list — on or after their scheduled weekday/day-of-month, so nothing is skipped just because the app stayed closed. Previous unfinished instances remain, and opening the app repeatedly in one logical period never creates duplicates.
- **Quick Subject Switching:** Clicking another subject (or pressing its number key) while tracking asks to switch — the current session keeps running until you confirm, so a misclick costs nothing.
- **Keyboard-first navigation:** `←`/`→` move between Goals, Subjects, Graphs, and Weight; `1`–`9` open that row's goal or start that subject; `Esc` leaves the Completed/Archived views; `W`/`M`/`Y`/`A` switch graph ranges; `⌘Z` undoes the last archive or reorder.
- **Offline & Private Sync:** SQLite keeps your tracking history available offline. When sync is enabled, changes go to your own server and connected devices. Every clean quit also writes a rotating JSON auto-backup (newest 10 kept) to a `backups/` folder beside your database.
- **Persistent sync feedback:** The phone and Mac headers show pending changes, connection problems and the last confirmed sync. Phone Settings gives the detailed status; the Mac offers Sync/Retry beside the status. A queued save stays saved on its device while waiting to send.
- **Accessible controls:** Keyboard focus stays visible through refreshes, sheets trap focus and return it on dismissal, and phone layouts support zoom and larger text. Weight plots offer expandable data tables. The phone respects reduced-motion preferences; Mac Settings has a local Reduce motion option.
- **Customized Aesthetics:** Ships out-of-the-box with custom animated rendering themes—such as dynamic Space Nebulas and minimal Checkerboards—to match your desktop preference.

## Project Structure

```text
JobTracker/
├── main.py                     # Native entry point
├── jobtracker/
│   ├── core/                   # Pure models, time, identity and plot math
│   │   ├── database.py         # Stable Database facade
│   │   └── persistence/        # Connection/schema and feature repositories
│   ├── services/               # TrackerService facade and feature rules
│   ├── sync/                   # Queued service, transport, orchestration
│   │   ├── mirror.py           # Feed row application and ordering
│   │   └── integrity.py        # Hashes, clock and report verification
│   └── ui/                     # Native window, page mixins and widgets
│       └── controllers/        # Keyboard and sync coordination
├── server/
│   ├── app.py                  # API composition and final static mount
│   ├── runtime.py              # Shared service state and lock
│   ├── routes/                 # Read/API routers and operation endpoint
│   ├── ops.py                  # Registry, atomic apply and retry receipts
│   └── operations/             # Explicitly registered domain handlers
├── web/
│   ├── app.js                  # Startup/navigation/refresh composition
│   ├── features/               # Subject, goal, session, graph, weight UIs
│   ├── data/                   # Credentials, API, mirror and ordered outbox
│   ├── shared/                 # DOM, sheets, fullscreen and formatting
│   └── styles/                 # Tokens, shell and common controls
├── tests/                      # Isolated Python, native and mobile checks
│   ├── support/                # Reusable fixtures and API/sync helpers
│   ├── ui/                     # Native feature regressions
│   └── mobile/                 # Node and mock-browser regressions
├── scripts/                    # Dev commands, static checks, safe packaging
├── docs/                       # Rules, architecture, generated maps/runbooks
├── deploy/                     # Own-server update and backup scripts
├── assets/                     # Icon artwork
└── JobTracker.spec             # PyInstaller packaging configuration
```

[Feature ownership](docs/generated/feature-index.md),
[operation handlers](docs/generated/operations.md) and
[facade delegation](docs/generated/interfaces.md) are checked source maps.
Compatibility imports remain available while implementations have focused owners.

## Phone App and Weight History

The phone app is served by your own authenticated JobTracker server. Its writes
use the same operations as the Mac app. A write is stored on the device before
it is sent, and reconnecting replays it in order using the original timestamps
and session identity. Locking the phone or restarting the server leaves an
unfinished timer intact.

The phone opens on **Weight** after a fresh launch. Returning to an app still
open in the background preserves its current tab and unfinished selection.
Use the plot's fullscreen button, then rotate the phone for a wider chart;
close fullscreen to return. Weight plot options and the time graphs remain
available. The agenda shows a current-time line in the current logical day,
updated each minute and when the app resumes.

On **Subjects**, tap a subject's **Add time** button to open Quick Add. Choose a
preset (including 90 minutes), or combine the custom duration buttons and save.
The session ends at the time you confirm, with no start/end fields to fill in.
A short confirmation names the saved range, and the earlier tracking day when
the session began before your day boundary. This also works offline and leaves
an active timer running. Tap the sync status under the page title for details
and anything still waiting to send.

On the Mac, local edits are sent to your server a moment after they are saved,
rather than waiting for the window to regain focus or the regular five-minute
sync. The status line above the pages shows when they have arrived.

To import an existing `Weight_graph` project, install the optional importer
dependency on the importing computer and run:

```sh
python3 -m pip install -r requirements-weight-import.txt
python3 -m server.import_weights --source ../Weight_graph --dry-run
python3 -m server.import_weights --source ../Weight_graph \
  --server https://your-server --token-file /path/to/sync_token
```

The importer reads the newest HealthManager CSV, then manual `WeightData.xlsx`
readings on days the scale does not cover, then `HistoricWeight.xlsx`. Repeated
imports preserve saved edits and never duplicate imported identities. Source
workbooks are retained unchanged. Weight uses calendar dates, independently of
the time tracker's logical day boundary. Full JSON backups include weight
readings and any offline identity aliases.

Restoring a backup with sync enabled queues the restored history durably for
your server. Retries preserve imported identities, existing server edits remain
authoritative, and device preferences stay on the importing device.

To feed the current server history back into an external plotting workflow:

```sh
python3 -m server.import_weights --source ../Weight_graph \
  --server https://your-server --token-file /path/to/sync_token \
  --export-json ../Weight_graph/data/JobTrackerWeights.json
```

## Local Setup

**Requirements:** macOS 12+ and Python 3.12+.

Clone the repository and set up your local environment:

```bash
git clone https://github.com/jobwestinga/JobTracker.git
cd JobTracker

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

To run the application in development mode:

```bash
python main.py
```
*(In development mode, your database is safely isolated at `./data/jobtracker.db`)*

## Development and Tests

Install the test dependencies in the chosen environment, then use the developer
commands from the repository root:

```sh
python -m pip install -r requirements-dev.txt
python scripts/dev.py context timer.sessions
python scripts/dev.py test --python /path/to/python
python scripts/dev.py check
python scripts/dev.py mobile
python scripts/dev.py browser
```

`test` runs isolated pytest tests, including offscreen native widgets. `mobile`
runs the Node regressions; `browser` runs four Playwright harnesses against
isolated mock data and storage. The [testing runbook](docs/runbooks/testing.md) explains focused
feature checks, interpreter selection and existing browser-tool configuration.
Tests use temporary databases and do not change your tracking history.

## How Days Are Counted

Tracked time is grouped by a **logical day** that starts at **03:00** by default,
not midnight — for most people a day "ends" when they sleep. So a session from
23:00 to 02:00 is counted on the day it started.

You can change this under **Settings → Day starts at** (`HH:MM`). The logical day
is used everywhere time is grouped: subject "Today" totals, the daily/weekly/
monthly graphs, the agenda timeline, and the daily-summary CSV. In the agenda
view, late-night work that belongs to the previous logical day appears at the
bottom of that day with labels like `01:00 (+1)`, so ordinary daytime days stay
compact.

## Goals and Recurring Templates

The **Goals** tab is for outcomes such as completing a project or earning a
qualification—not hour quotas. A goal can have a description and
milestones with their own titles and descriptions. Check milestones off in the
goal detail view; completed milestones stay visible. A goal can only be
completed once every milestone is checked, and the **Completed** view (or `Esc`
to leave it) lists finished goals so any of them can be reopened. Goal editing
also provides the confirmed permanent-delete action.

Right-click a goal (or use the ☆ button) for **Focus This Week**. That is purely
a visual marker for the goals you're concentrating on — it never changes
ordering, completion rules, or template generation, and it is preserved in
backups.

Use **Goals → Templates** for routine packs such as a daily practice checklist,
weekly review, or monthly admin task. When a new logical day/week/month begins,
JobTracker creates one normal, editable goal from each active template — weekly
and monthly templates fire on or after their chosen weekday/day-of-month, so a
template is never skipped just because the app stayed closed that day. A
template title may include `{date}`, which is replaced with the instance's date.
Older unfinished instances are left untouched.

## Graphs

The stacked-bar and agenda views offer calendar-aligned **Weeks** (previous
Monday through today), **Months** (the previous month through today), **Year**
(January 1 of the current year through today), **All Time**, and custom ranges.
Stacked bars can be grouped daily, by ISO week number, or by calendar month.

The graph settings dialog also stores the agenda's visible hour window (default
06:00–23:00, with an optional auto-fit to the sessions actually shown) and
whether the chart is scaled to the window width. Grouping can follow the
selected range automatically or be overridden with daily, weekly or monthly
buckets. On the phone, both graphs offer fullscreen viewing and the agenda
marks the current time in the current logical day's column.

## Exporting Your Data

**Settings → Export Backup** writes a `.zip` bundle containing:

- `jobtracker_backup.json` — the authoritative full backup (the only file used to
  restore, including goals, templates, milestones, and settings; **Import
  Backup** accepts this `.json`, or the `.zip` directly)
- `sessions.csv`, `subjects.csv` — human-readable, open in Numbers/Excel
- `daily_summary.csv` — per-day, per-subject totals (respects your day-start)
- `README.txt` — explains the files

## Deleting Subjects

Deleting a subject also deletes its sessions. If a subject has tracked time, a
strong confirmation appears showing how many sessions and how much time would be
lost, recommends archiving instead, and requires you to type the subject name (or
`DELETE`) to proceed. Subjects with no sessions delete with a simple confirm.

## Building and Updating the macOS App

Build a verified, signed bundle outside iCloud without replacing the installed app:

```sh
python scripts/dev.py build --python /path/to/python
```

For an authorized installation, add `--install`. The guarded installer checks all
required databases for open sessions and pending writes, takes online backups,
and preserves the previous bundle. Busy installations wait for safe idle;
application rollback never overwrites your data. See the
[build/install runbook](docs/runbooks/build-install.md) for staged installation,
status, required databases and legacy build-script behavior.

Server and phone updates use the [deployment runbook](docs/runbooks/deploy.md).
If something fails, follow the [incident runbook](docs/runbooks/incidents.md)
before attempting a repair or restore.

## Data Storage & Backups

When packaged as an `.app`, JobTracker writes all user data to `~/Library/Application Support/JobTracker/jobtracker.db`, with the automatic JSON backups in `~/Library/Application Support/JobTracker/backups/`. In development mode both live under `./data/` instead. This ensures your tracking history persists securely and isn't lost if you rebuild or upgrade the `.app` bundle in the future.

If you ever need to migrate data between machines, or between your code environment and your packaged app, use the **Export/Import Backup** functionality securely tucked away in the in-app Settings panel. It packages everything into easily portable JSON files.

## Customizing the Icon

Place the PNG artwork at `assets/icon.png` and use the safe build command.
The builder generates and bundles the macOS icon in its staged source copy.

## Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the issues page if you want to contribute.

## License

This project is open-source and available under the MIT License.
