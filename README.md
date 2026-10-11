# JobTracker

JobTracker is a native macOS desktop application designed to capture and visualize how you spend your time. It provides a distraction-free, fully local environment to track recurring workflow or study sessions without relying on cloud services or electron constraints. Built with Python and PySide6, the application prioritizes speed, data ownership, and aesthetic polish.

## Features

- **Daily weight on phone and Mac:** The Weight tab has seven whole-kilogram buttons centered on your latest reading and ten decimal buttons (.0–.9). Tap one in each row, then Save. On the phone, both rows span the available width and a durable save clears the selection. Entry controls adjust dates, tile size and contrast. Entries use calendar days, and saving again on the same day updates the manual entry. Offline saves enter a durable queue; previous readings can be edited or deleted.
- **Personal weight plots:** Choose 30/90 days, six months, a year, all history, or custom dates. The original LOESS trend uses a 4% span, with smoother options, a seven-day average, measurement visibility, historic readings, and an optional zero-based axis. Sparse historic readings use shape-preserving interpolation. Export the plot as PNG or the measurements as CSV.

- **Live Session Tracking:** Create color-tagged subjects and track your progress with a single click. Inline card actions (edit, manage sessions, archive) keep everything one tap away, and the active topic stays prominent while others dim. A live persistent dashboard tracks your active sessions. New subjects suggest colors that stay visually distinct from your existing ones.
- **Crash-safe recovery:** If the app or laptop dies mid-session, on reopen JobTracker asks how to handle the unfinished session — end it at the last time it was known active, end it now, or set a custom end time/length. It never silently counts hours you weren't working, and never deletes the session for you.
- **Detailed History & Visualizations:** Modify, backfill, or manage past sessions seamlessly—quick-add a preset or custom duration ending now, drop a session into a fixed time slot, duplicate one to today or the next day, or nudge it ±15m/±1h without retyping any dates. Use stacked bar charts (grouped **daily, weekly, or monthly**) and compact agenda timelines. Pick a preset range (Weeks/Months/Year/All) or a custom date range. Hover a bar or agenda block for details, click a block to edit that exact session, or click a day to inspect everything on it.
- **Outcome-focused Goals:** Keep goals separate from timed subjects. Add a description and milestone checklist, track progress, complete a goal only after its milestones are done, and reopen completed goals at any time. Star the handful you're concentrating on with **Focus This Week** so they stand out in the list.
- **Recurring Goal Templates:** Daily, weekly, or monthly templates generate ordinary goals at the top of the list — on or after their scheduled weekday/day-of-month, so nothing is skipped just because the app stayed closed. Previous unfinished instances remain, and opening the app repeatedly in one logical period never creates duplicates.
- **Quick Subject Switching:** Clicking another subject (or pressing its number key) while tracking asks to switch — the current session keeps running until you confirm, so a misclick costs nothing.
- **Keyboard-first navigation:** `←`/`→` move between Goals, Subjects, and Graphs; `1`–`9` open that row's goal or start that subject; `Esc` leaves the Completed/Archived views; `W`/`M`/`Y`/`A` switch graph ranges; `⌘Z` undoes the last archive or reorder.
- **Offline & Private Sync:** SQLite keeps your tracking history available offline. When sync is enabled, changes go to your own server and connected devices. Every clean quit also writes a rotating JSON auto-backup (newest 10 kept) to a `backups/` folder beside your database.
- **Customized Aesthetics:** Ships out-of-the-box with custom animated rendering themes—such as dynamic Space Nebulas and minimal Checkerboards—to match your desktop preference.

## Project Structure

An overview of the codebase to help you navigate:

```text
JobTracker/
├── main.py                   # Main application entry point
├── build_macos.sh            # Automated PyInstaller build script
├── JobTracker.spec           # PyInstaller packaging configuration
├── assets/                   # App icons and related assets
├── tests/                    # Isolated pytest, offscreen Qt and mobile regressions
├── server/                   # Authenticated API, operation handlers and imports
├── web/                      # Plain ES-module phone app and offline shell
├── deploy/                   # Own-server deployment and backup scripts
├── MOD/                      # Modularity/reference proposal (PDF and Markdown)
└── jobtracker/               # Core application package
    ├── core/                 # SQLite database, models, time/day math, themes
    ├── services/             # Shared tracking and weight business logic
    ├── sync/                 # Durable outbox, server transport and local mirror
    └── ui/                   # PySide6 window, page mixins, widgets, styles
```

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

**Requirements:**
- macOS 12+ (Monterey or later)
- Python 3.12+ 

Clone the repository and set up your local environment:

```bash
git clone https://github.com/jobwestinga/JobTracker.git
cd JobTracker

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

To run the application in development mode:

```bash
python main.py
```
*(In development mode, your database is safely isolated at `./data/jobtracker.db`)*

## Running the Tests

The core logic (database, time/day calculations, sessions, import/export) is
covered by an isolated pytest suite that never touches your real database.
Native widget tests use offscreen Qt; mobile JavaScript regressions use Node
when available. The separate browser smoke harness lives in `tests/mobile/`.

```bash
# Install dev dependencies (pytest) on top of the runtime requirements
pip install -r requirements-dev.txt

# Run the suite
python -m pytest
```

Each test uses its own temporary SQLite database, so running the tests is always
safe and will not modify `data/jobtracker.db`.

## How Days Are Counted

Tracked time is grouped by a **logical day** that starts at **03:00** by default,
not midnight — for most people a day "ends" when they sleep. So a session from
23:00 to 02:00 is counted on the day it started.

You can change this under **Settings → Day starts at** (hour selector). The logical day
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

## Building the macOS App

You can package JobTracker into a standalone macOS `.app` bundle. This encapsulates the Python environment and creates a clean executable you can move to your `/Applications` folder.

Run the included build script:
```bash
./build_macos.sh
```

By default, the script places the finished application directly into `/Applications/JobTracker.app` and cleans up temporary build artifacts to keep your workspace tidy.

> **Note on Gatekeeper:** Because this application is not distributed through the Mac App Store and isn't inherently code-signed, macOS Gatekeeper will block it on the first launch. You can bypass this by right-clicking the app and selecting **Open**, or by clearing the quarantine flag via your terminal: `xattr -cr /Applications/JobTracker.app`

## Data Storage & Backups

When packaged as an `.app`, JobTracker writes all user data to `~/Library/Application Support/JobTracker/jobtracker.db`, with the automatic JSON backups in `~/Library/Application Support/JobTracker/backups/`. In development mode both live under `./data/` instead. This ensures your tracking history persists securely and isn't lost if you rebuild or upgrade the `.app` bundle in the future.

If you ever need to migrate data between machines, or between your code environment and your packaged app, use the **Export/Import Backup** functionality securely tucked away in the in-app Settings panel. It packages everything into easily portable JSON files.

## Customizing the Icon

To inject your own custom branding, simply drop a new PNG image into the `assets/` directory named `icon.png` (overwriting the existing one) and rebuild the application. The build script will automatically bundle it and purge the macOS icon caches for you.

## Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the issues page if you want to contribute.

## License

This project is open-source and available under the MIT License.
