# JobTracker

JobTracker is a native macOS desktop application designed to capture and visualize how you spend your time. It provides a distraction-free, fully local environment to track recurring workflow or study sessions without relying on cloud services or electron constraints. Built with Python and PySide6, the application prioritizes speed, data ownership, and aesthetic polish.

## Features

- **Live Session Tracking:** Create color-tagged subjects and track your progress with a single click. Inline card actions (edit, manage sessions, archive) keep everything one tap away, and the active topic stays prominent while others dim. A live persistent dashboard tracks your active sessions.
- **Detailed History & Visualizations:** Modify, backfill, or manage past sessions seamlessly—including duration and fixed time-slot quick-adds for fast backfilling. Gain insights through custom-built stacked bar charts and daily agenda timelines that illustrate exactly where your day went.
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
