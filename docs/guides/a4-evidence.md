# A4 evidence record

Companion to the [A4 guide](a4-native-mac-overhaul.md). One compact record per
milestone: what was run, on what, with which result. Synthetic data only;
captures and timings live in the ignored `.local/a4-evidence/` folder.

The subsequent [independent correctness review](a4-review-2026-10-22.md) records
regressions found and fixed after `165d6e4`. Historical test counts below describe
that earlier implementation. A row with outstanding runtime evidence is Partial,
even when its available unit/model checks pass; this is not a native cutover signoff.

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

## M1–M4 — native proof, vertical slice, workflows and candidate, 22 October 2026

What exists now (commits `0201102`…`f0587ce`):

| Phase | Delivered | Owner |
| --- | --- | --- |
| P01 | F1 roles in `design/ui_roles.json`; generated Swift tokens and phone role block from `core/themes.py`; shared components; S1 cards; compact eight-hue colour choice; vector icons | [generator](../../scripts/generate_ui_tokens.py) |
| P02 | Qt-free engine host: explicit store modes, crash-safe single-owner lock (also in `main.py`), facade reuse, Qt-free sync worker and schedules, quit/EOF/SIGTERM lifecycle | [native engine](../architecture/native-engine.md) |
| P03 | Versioned NDJSON bridge, command allowlist, outcomes, additive receipts with pre-migration backup, shared fixtures and live samples | `jobtracker/desktop/` |
| P04–P13 | SwiftUI app: shell, Subjects/timer/recovery, Goals/milestones/templates, W1 session panel, R1 bars, Bold agenda, Weight, Settings/sync/backup, key routing, single undo | [native/macos](../../native/macos/README.md) |
| P14 | Phone: Bold agenda (joins, honest lanes, duration line), R1 soft bloom, compact colours that keep saved values exactly; shell v26 | `web/features/graphs`, `web/features/subjects` |
| P15 | Signed bundle with frozen Qt-free engine (pilot and production identities), build CLI, installer guards for native processes and unresolved requests | [build_native.py](../../scripts/build_native.py) |

### Checks

| Check | Result |
| --- | --- |
| `dev.py native` (JobTrackerChecks: layouts, fixtures, journal, live engine, feature flows) | 37 checks, 0 failures |
| `pytest tests/desktop` (engine protocol, commands, lifecycle, synthetic store) | all pass |
| `dev.py mobile` | 151 passed (148 + 3 agenda-layout) |
| `dev.py browser` | all four harnesses passed (`voiceOverTested: false`) |
| `dev.py check` | references, boundaries, tokens, check index, offline assets, privacy pass |
| Packaged helper smoke | frozen engine answers from a neutral folder, `qt_loaded: false` |

### Performance (synthetic store, same Mac)

| Measure | Qt (source, on-screen) | Native (packaged pilot) |
| --- | --- | --- |
| Launch to first data | 0.73–0.75 s warm, 1.41 s cold (import + window) | 0.28–0.34 s warm, 0.69–0.76 s first launches (app start to subjects loaded) |
| Peak memory | 173–183 MB (one process) | 128–136 MB (app 94–100 MB + engine 32–36 MB) |
| Idle CPU over 20 s (static theme, timer running) | 0.03 CPU-s | 0.04 CPU-s (app + engine) |
| Bundle size | 96 MB installed app | 28 MB |

The two launch columns are not the identical method: Qt is measured from a
source launch (import + first window), native from process start to the first
subjects response. Neither difference exceeds the 10% regression threshold in
the wrong direction. Starting the engine before AppKit finished launching cut
native first data from ~1.07 s to ~0.3 s.

### Rollback rehearsal (§9)

On a private online-backup copy of the installed store (deleted afterwards):
the engine added `desktop_request_receipts` after writing a pre-migration
backup; data row counts were unchanged; the only write was the one queued
`generate_due_goals` that any synced launch performs. The previous Qt build's
source (`b49f093`) opened the upgraded copy, listed the same 20 subjects and
exported the same tables. A code rollback needs no data change.

### Visual evidence

Native captures for all six themes, every page and both graph modes at
680×920 and 560×760 (2x), plus panels, under `.local/a4-evidence/native-m1/`.
Half-width windows keep every subject action reachable; agenda day labels
thin instead of overlapping; titles and durations fit or step down, never clip.

## M5 — release-candidate checks (P16.1)

| Check | Result |
| --- | --- |
| `dev.py test` (full pytest, Qt included) | 1086 passed, 22 skipped (the baseline's skips), 664 s |
| `dev.py native` | 37 checks, 0 failures |
| `dev.py mobile` / `dev.py browser` | 151 passed / all four harnesses passed |
| `dev.py check` | pass |

The Qt app and `python main.py` remain the default and pass their unchanged
regressions; the only Qt-side changes are shared extractions and the store lock.

## Acceptance matrix status (P16.2)

Evidence kinds: **E** engine/Python test, **N** native check (`dev.py native`),
**P** phone harness, **V** native capture reviewed, **M** manual only.

| ID | Status | Evidence | Remaining |
| --- | --- | --- | --- |
| A01 | Partial | N key routing (Left/Right, held key) | Real-keyboard XCUI run |
| A02 | Pass | N digits ignored outside Goals; V no numbers in sidebar/Subjects/graph modes | — |
| A03 | Partial | N digit opens exact row; router ignores pointer hover | XCUI with hover |
| A04 | Partial | N digit toggles milestone in top panel; repeat ignored in router | Text-entry and held-key UI run |
| A05 | Partial | N Escape closes panel and restores theme; fullscreen/view order in `AppModel.escape` | Real fullscreen Space run |
| A06 | Pass | E resume history; N stop → Resume | — |
| A07 | Pass | E short-stop discard, exact session; N confirmed switch | — |
| A08 | Pass | E crash, EOF, SIGTERM, restart, foreign timer; N foreign timer respected | — |
| A09 | Partial | E reorder/archive; N archive + undo; P exact colours | Native drag-reorder UI run |
| A10 | Pass | N completion gated; E focus/complete outcomes | — |
| A11 | Pass | E launch/entry generation through the facade | — |
| A12 | Pass | N agenda block and subject history select exact UID | — |
| A13 | Pass | V W1 presets visible without disclosure; P phone presets | — |
| A14 | Pass | N two +15m moves keep UID and duration; moving out of the day clears selection and later taps act on nothing | — |
| A15 | Pass | E/N short edit refused, original intact, exact unchanged endpoints | — |
| A16 | Pass | N/P honest overlap lanes; synthetic future/overlap rows | — |
| A17 | Pass | No client date rules; engine reuses services/graph window | — |
| A18 | Pass | N Up/Down only with Graphs owning input | — |
| A19 | Partial | N tiers/scale/peak/bloom cache; P red bloom; V captures | 200% text on Mac |
| A20 | Partial | N proportional, contiguous, non-overlapping labels | Long-history timing |
| A21 | Pass | N/P adjacency tolerance and lanes; V no divider | — |
| A22 | Pass | N preview, Escape rollback, save pair | — |
| A23 | Partial | P untouched colour kept exactly; native editor starts from saved value | Native colour UI run |
| A24 | Pass | N imported precision and daily identity; E weight.view | — |
| A25 | Partial | E trend/badge from `weight_plot`; fullscreen/Escape in code | Native fullscreen UI run |
| A26 | Pass | E local-only and synced queueing; engine thread owns network | — |
| A27 | Partial | N unreachable server shows Offline, token hidden, local save queued; shared wording | Blocked-server UI run |
| A28 | Partial | E export bundle, import merge, malformed import | Native file-dialog cancel run |
| A29 | Not done | V half-width only | **M** VoiceOver, display scaling, reduced motion |
| A30 | Partial | N drafts bound to UID; epochs | Sync-during-edit UI run |
| A31 | Pass | P profiles unchanged | — |
| A32 | Partial | E Qt entry lock, installer guards; rollback rehearsal; signed package | Live guarded install |

## Failure matrix status

| Failure | Status | Evidence |
| --- | --- | --- |
| UI closes before sending | Pass | Journal written before send; nothing applied |
| UI disconnects after send | Pass | N journal replay with original IDs after restart |
| Commit then reply lost | Pass | E replay returns receipt; N `replayed` exactly once |
| Receipt persistence fails | Pass | E mutation and outbox roll back, timer re-read |
| Store full/unwritable | Partial | `storage_error` path and draft retention in code; no disk-full injection |
| Engine crash with timer open | Pass | E SIGKILL → session preserved, recovery prompt next owner; N reconnect |
| UI crash, engine EOF | Pass | E EOF/SIGTERM keep the session, clean exit |
| Second owner | Pass | E/N `store_in_use` before recovery; Qt entry refuses |
| Server slow/offline/TLS | Pass (existing) | Sync engine tests; worker off the command thread |
| Ack lost at server | Pass (existing) | Operation contract tests |
| Target deleted elsewhere | Pass | E `missing`; N selection cleared, never another row |
| Sync changes an open draft | Partial | Conflict prompt in W1 reconcile; no two-device run |
| Engine restart / stale response | Pass | Instance IDs and epochs; N reconnect |
| Outbox nonempty at install | Pass | Installer guards (existing + native journal) |
| Theme preview cancelled | Pass | N Escape restores the saved appearance |
| Backup cancelled/failed | Pass | E malformed import changes nothing; export via temp file |
| Mirror repair after local command | Pass | E receipts survive mirror replacement |
| Install fails mid-replacement | Pass (existing) | Installer rollback tests |
| Phone storage/credential failure | Pass (existing) | Phone harnesses |

## Not done, and why

- **XCUI automation and VoiceOver (A29, P16.4).** Full Xcode is not installed;
  the guide forbids installing it silently. The Command Line Tools also lack
  the SwiftUI/Testing macro plugins, so native checks run as an executable and
  drive stores and the key router directly. VoiceOver itself is built into
  macOS and does not require Xcode. A manual VoiceOver and keyboard pass remains
  required before any default switch.
- **Production pilot install and default switch (P17.3–P17.6).** The signed
  production-identity package is staged and accepted by the installer's
  verifier, but nothing was installed: the guide does not let performing it
  trigger installation, and A29 and the manual pilot are open gates.
- **Phone deployment (P14.6).** Assets are tested locally (shell v26) but not
  deployed to the server for the same reason.
