# A4 evidence record

Companion to the [A4 guide](a4-native-mac-overhaul.md). One compact record per
milestone: what was run, on what, with which result. Synthetic data only;
captures and timings live in the ignored `.local/a4-evidence/` folder.

## M0 — baseline (P00), 22 October 2026

### Reproducibility

| Item | Value |
| --- | --- |
| Source | `b49f093` (guide); latest app-code change `925389c`; untracked `october-21-2026-redesign/` preserved |
| Mac | macOS 27.0.1 (26A434), arm64 |
| Toolchain | Swift 6.4 (Command Line Tools); **full Xcode not installed**, `xcodebuild` unavailable |
| Python | 3.12.10 (`/usr/local/bin/python3`, pytest, PySide6, fastapi, httpx, PyInstaller 6.21) |
| Node | 26.8.2; Playwright from `.local/browser-tools` via `PLAYWRIGHT_MODULE` |
| Fixture | [synthetic_store.py](../../tests/support/synthetic_store.py), fixed clock 2026-10-21 16:20, stable UUIDv5 identities |

### Existing setup (P00.3)

| Concern | Current behaviour |
| --- | --- |
| Identity | `JobTracker`, bundle `com.jobtracker.app`, Cutout J icon, version 1.0.0 |
| Platform | `LSMinimumSystemVersion` 12.0; installed bundle arm64, ad-hoc signed |
| Stores | Packaged: `~/Library/Application Support/JobTracker/jobtracker.db`; source: `data/jobtracker.db`; `JOBTRACKER_DB_PATH` overrides both |
| Credentials | `sync_token` (0600) beside the store, `JOBTRACKER_SYNC_TOKEN_PATH` override; server URL and on/off in local settings |
| Backups | Rotating `backups/autobackup_*.json` (10 kept) on clean quit; manual ZIP/JSON export and merge import |
| Lifecycle | Launch: recovery through the selected facade, due recurring goals, sync after 1.2 s; quit refused while genuinely tracking; heartbeat 60 s |
| Installer | Matches `JobTracker.app/Contents/MacOS/JobTracker`; reads both stores read-only; open sessions or pending outbox block replacement |

### Behaviour inventory (P00.2)

Every entry point and the engine command that will serve it. The feature
owners are listed by `python3 scripts/dev.py context FEATURE_ID`.

| Area | Qt entry points | Service / engine command |
| --- | --- | --- |
| Shell | Sidebar Goals/Subjects/Graphs/Weight, gear, sync status button, active-timer return button; Left/Right pages; Escape; Cmd+Z single undo | `settings.get`, `sync.status`, `timer.get`, `app.foreground`, `app.quit` |
| Subjects | + Subject, Edit (name/colour/notes), Sessions, Archive/Unarchive, Archived view, delete with summary/Archive Instead, drag reorder, Total/Today/7/30-day filter, Stop, Resume, start/switch with confirmation, recovery prompt | `subjects.list`, `subject.*`, `timer.start/stop/switch`, `recovery.resolve/dismiss`, `subject.deletion_summary` |
| Sessions | Manage Sessions, Add Session, Edit, Delete, Duplicate to Today/+1 Day, ±15m/±1h/±1 day move, quick-add presets and Custom, day history from graphs, agenda block editor | `sessions.for_subject` (paged), `sessions.for_day`, `session.get/add/update/delete/shift/duplicate` |
| Goals | + Goal, Edit/Delete Goal, Completed view, Mark Complete/reopen, focus star, milestones add/edit/toggle/delete/reorder, drag reorder, digits 1–9, Templates (New/Edit/Disable/Delete) | `goals.list`, `goal.*`, `milestone.*`, `templates.list`, `template.*`, `goals.generate_due` |
| Graphs | Stacked Bar / Agenda (Up/Down), Graph Settings (range, custom dates, grouping, hours, autofit, fit width), W/M/Y/A ranges, day click, block click | `graphs.breakdown`, `graphs.agenda`, `settings.set` |
| Weight | Date picker/Today, whole and decimal tiles, Update weight, Options, range/curve/dots/historic/span, Fullscreen, history search, Edit/Delete selected | `weight.list`, `weight.upsert`, `weight.delete`, `settings.set` |
| Settings | Theme (six named + kept legacy pair, live preview, Save/Cancel), Reduce motion, Day starts at, sync enabled/URL/token/Sync Now, Export/Import backup | `settings.set`, `sync.configure`, `sync.now`, `backup.export`, `backup.import` |

### Checks (P00.6)

| Check | Result |
| --- | --- |
| `dev.py test` (full pytest) | 1025 passed, 22 skipped, 710 s |
| `dev.py mobile` | 148 passed |
| `dev.py browser` | all four harnesses passed (`voiceOverTested: false`) |
| `dev.py check` | references, boundaries, offline assets and privacy pass |

### Qt visual and launch baseline (P00.5)

Offscreen captures of all four pages (Graphs in both modes) for the six named
themes at 680×920 and 560×760, at 1x and 2x: 120 images under
`.local/a4-evidence/baseline-qt/`. Launch on the synthetic store, offscreen,
source build (import + first window): 0.15–0.38 s import, 0.45–0.81 s window,
116–133 MB peak RSS. Offscreen numbers are not the installed app's
on-screen launch; the native comparison repeats the same method.
