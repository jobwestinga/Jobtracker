# JobTracker

JobTracker is a native macOS desktop application designed to capture and visualize how you spend your time. It provides a distraction-free, fully local environment to track recurring workflow or study sessions without relying on cloud services or electron constraints. Built with Python and PySide6, the application prioritizes speed, data ownership, and aesthetic polish.

## Features

- **Live Session Tracking:** Create color-tagged subjects and track your progress with a single click. Inline card actions (edit, manage sessions, archive) keep everything one tap away, and the active topic stays prominent while others dim. A live persistent dashboard tracks your active sessions. New subjects suggest colors that stay visually distinct from your existing ones.
- **Crash-safe recovery:** If the app or laptop dies mid-session, on reopen JobTracker asks how to handle the unfinished session — end it at the last time it was known active, end it now, or set a custom end time/length. It never silently counts hours you weren't working, and never deletes the session for you.
- **Detailed History & Visualizations:** Modify, backfill, or manage past sessions seamlessly—including duration and fixed time-slot quick-adds for fast backfilling. Gain insights through custom-built stacked bar charts (grouped **daily, weekly, or monthly**) and daily agenda timelines that illustrate exactly where your day went. Pick a preset range (7/14/30/All) or a one-off custom date range.
- **Integrated To-Do Lists:** Separate from your timed subjects, standard tasks allow you to set deadlines and manage direct task completion workflows.
- **Offline & Fully Local:** Your data never leaves your computer. Backed by a local SQLite engine, all your tracking history is private and portable.
- **Customized Aesthetics:** Ships out-of-the-box with custom animated rendering themes—such as dynamic Space Nebulas and minimal Checkerboards—to match your desktop preference.

## Project Structure

An overview of the codebase to help you navigate:

```text
JobTracker/
├── main.py                   # Main application entry point
├── build_macos.sh            # Automated PyInstaller build script
├── JobTracker.spec           # PyInstaller packaging configuration
├── assets/                   # App icons and related assets
└── jobtracker/               # Core application package
    ├── core/                 # SQLite database, models, and global configs
    ├── services/             # Business logic and tracker state
    └── ui/                   # PySide6 UI views, widgets, and styles
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
covered by a headless pytest suite that never touches your real database.

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

## Exporting Your Data

**Settings → Export Backup** writes a `.zip` bundle containing:

- `jobtracker_backup.json` — the authoritative full backup (the only file used to
  restore; **Import Backup** accepts this `.json`, or the `.zip` directly)
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

When packaged as an `.app`, JobTracker writes all user data to `~/Library/Application Support/JobTracker/jobtracker.db`. This ensures your tracking history persists securely and isn't lost if you rebuild or upgrade the `.app` bundle in the future.

If you ever need to migrate data between machines, or between your code environment and your packaged app, use the **Export/Import Backup** functionality securely tucked away in the in-app Settings panel. It packages everything into easily portable JSON files.

## Customizing the Icon

To inject your own custom branding, simply drop a new PNG image into the `assets/` directory named `icon.png` (overwriting the existing one) and rebuild the application. The build script will automatically bundle it and purge the macOS icon caches for you.

## Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the issues page if you want to contribute.

## License

This project is open-source and available under the MIT License.
