# JobTracker

A polished personal desktop time-tracking app for macOS.  
Track how long you spend on recurring tasks — fully local, fully offline.

---

## What the App Does

- **Subjects (timed)** — create colour-tagged subjects and track them by clicking anywhere on the subject card.
- **Live timer display** — prominent "Currently Tracking" card with a running clock.
- **Automatic stop on quit** — if you close the app while a timer is running, the session is saved.
- **Full session history** — view, add, edit, or delete past sessions per subject.
- **Subject statistics** — see cumulative time per subject filtered by Total / Last 7 days / Last 30 days.
- **Tasks (completable)** — separate from subjects, with optional deadlines, completion toggles, and manual ordering.
- **Graphs window** — last 10 days as stacked daily bars, colour-segmented by subject time.
- **Bottom navigation bar** — switch between Subjects / Tasks / Graphs environments.
- **Settings gear** — open Theme FX + Colour Palette controls.
- **Export / Import** — available in **Settings** to back up all data to JSON and restore it later.
- **Distinct FX themes** — animated subtle background treatments for Clean / Glow / Glassmorphism / Neon.

---

## Project Structure

```
Jobtracker/
├── main.py                   ← Entry point
├── requirements.txt          ← PySide6 + PyInstaller
├── JobTracker.spec           ← PyInstaller config
├── build_macos.sh            ← One-command build script
├── build.py                  ← Legacy build helper
├── README.md
├── .gitignore
├── assets/                   ← Icons (bundled into .app)
│   └── icon.icns             ← Replace with your app icon
├── data/                     ← Dev-mode SQLite database (gitignored)
└── jobtracker/
    ├── __init__.py
    ├── core/
    │   ├── __init__.py
    │   ├── config.py          ← Paths, design tokens, frozen/dev detection
    │   ├── database.py        ← SQLite schema + CRUD
    │   └── models.py          ← Subject / TodoTask / Session dataclasses
    ├── services/
    │   ├── __init__.py
    │   └── tracker_service.py ← Business logic layer
    └── ui/
        ├── __init__.py
        ├── app.py             ← Main window
        ├── styles.py          ← Global stylesheet
        └── widgets/
            ├── __init__.py
            ├── active_timer.py
            ├── fx_background.py
            ├── graphs_view.py
            ├── subject_item.py
            ├── subject_dialog.py
            ├── todo_task_item.py
            ├── todo_task_dialog.py
            ├── session_dialog.py
            ├── manage_sessions_dialog.py
            └── settings_dialog.py
```

---

## Setup

### Prerequisites

- **macOS 12+** (Monterey or later)
- **Python 3.12+** — check with `python3 --version`

### Install

```bash
cd ~/Desktop/Jobtracker

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Running Locally

```bash
cd ~/Desktop/Jobtracker
source venv/bin/activate
python main.py
```

In development mode the database is stored at `./data/jobtracker.db`.

---

## Building the macOS `.app`

### One-command build (recommended)

```bash
cd ~/Desktop/Jobtracker
./build_macos.sh
```

This script will:
1. Create a virtual environment if one doesn't exist
2. Install/update all dependencies
3. Clean previous build artifacts
4. Run PyInstaller with the `.spec` configuration
5. Replace `/Applications/JobTracker.app` (default)
6. Print run/install locations when done

### Manual build

```bash
cd ~/Desktop/Jobtracker
source venv/bin/activate
pyinstaller JobTracker.spec --noconfirm
```

### Output

- Installed app (default): `/Applications/JobTracker.app`
- Local `dist/` and `build/` artifacts are removed by default for a cleaner workspace.

To keep the generated `dist/JobTracker.app` (and build artifacts), run:

```bash
KEEP_DIST_APP=1 ./build_macos.sh
```

---

## Where Data Is Stored

| Mode | Database location |
|------|-------------------|
| Development (`python main.py`) | `./data/jobtracker.db` |
| Packaged (`.app` bundle) | `~/Library/Application Support/JobTracker/jobtracker.db` |

The `.app` bundle itself is read-only. All user data is written to the
Application Support directory so upgrades or re-installs don't lose your data.

---

## Export / Import Backups

### Export

1. Click the **⚙ Settings** button in the app header.
2. Click **Export Backup**.
3. Choose a save location — the file is standard JSON.

### Import

1. Click the **⚙ Settings** button in the app header.
2. Click **Import Backup**.
3. Select a previously exported `.json` file.
4. Confirm the merge — existing subjects are matched by name, sessions are de-duplicated.

Backups are fully portable. You can export on one machine and import on another.

---

## Replacing the App Icon

If your file is named `JobTracker.png`, move it to:

```bash
mv JobTracker.png assets/icon.png
```

Then rebuild with `./build_macos.sh`.

The build script auto-generates `assets/icon.icns` from `assets/icon.png`
and strips stale macOS metadata from the bundle, so icon updates refresh
more reliably in Applications and Launchpad.

---

## Troubleshooting

### "App is damaged" / Gatekeeper blocks the app

The `.app` is not code-signed. macOS Gatekeeper will block it on first launch.

**Fix:**

```bash
xattr -cr /Applications/JobTracker.app
```

Then double-click to open, or right-click → **Open** → confirm.

### App opens but window is blank or crashes

Make sure you built with the **same Python version** as your venv.  
Clean build and retry:

```bash
rm -rf build dist
./build_macos.sh
```

### "No module named PySide6"

Your venv is not activated. Run:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Database is empty after packaging

This is expected. The packaged app uses a different data directory
(`~/Library/Application Support/JobTracker/`) than development mode (`./data/`).

To migrate your data: **Export** from the dev version, then **Import** into the packaged version.

### Bundle size is ~300–400 MB

Normal for PySide6/Qt apps. Qt libraries are large. The app excludes unused
Qt modules (3D, WebEngine, Multimedia, etc.) to reduce size where possible.

---

## Known Limitations

- **Not code-signed** — Gatekeeper will warn on first launch (see troubleshooting above).
- **No auto-update** — to upgrade, rebuild and replace the `.app`.
- **Subject name uniqueness** is case-insensitive. Renaming "Work" to "WORK" is treated as the same name.
- **Single-user** — no multi-user or sync features.
