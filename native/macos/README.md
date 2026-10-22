# JobTracker native Mac frontend

SwiftUI with small AppKit adapters, over the bundled Python engine
([`jobtracker.desktop`](../../docs/architecture/native-engine.md)). The engine
owns data, timer, recovery, scheduling and sync; Swift owns presentation state.
Minimum macOS 12. Apple frameworks only.

## Layout

| Path | Owns |
| --- | --- |
| `Sources/JobTrackerKit/Bridge` | Engine child process, NDJSON framing, handshake, request journal, launch configuration |
| `Sources/JobTrackerKit/Model` | DTOs matching `jobtracker/desktop/dto.py`; naive local time display and capture |
| `Sources/JobTrackerKit/Stores` | F2 presentation state: page, panel stack, drafts, selections, topic routing, single undo |
| `Sources/JobTrackerKit/Theme` | Generated F1 tokens (`TokensData.generated.swift`), themes, shared components |
| `Sources/JobTrackerKit/Charts` | Stacked bars with R1 cached bloom, Bold agenda, weight plot, AppKit hit testing |
| `Sources/JobTrackerKit/Views` | Shell, sidebar, pages and window-local panels (W1 session panel) |
| `Sources/JobTrackerKit/KeyRouter.swift` | Page arrows, Goals digits, graph keys, Escape order |
| `Sources/JobTrackerChecks` | Macro-free check runner (layouts, fixtures, journal, live engine) |

## Build, run and check

Build products stay outside the repository and iCloud:

```sh
swift build --package-path native/macos --scratch-path ~/Library/Caches/JobTracker/native-build
python3 scripts/dev.py native --python /path/to/python      # build + JobTrackerChecks
```

A development run needs an engine interpreter and checkout, and must select a
disposable or pilot store explicitly (the default is the isolated pilot store
under `~/Library/Application Support/JobTracker Native Pilot/`, never the
Qt app's data):

```sh
JOBTRACKER_ENGINE_PYTHON=/path/to/python JOBTRACKER_ENGINE_ROOT="$PWD" \
JOBTRACKER_NATIVE_MODE=isolated JOBTRACKER_NATIVE_DB=/tmp/jt-pilot/store.db \
~/Library/Caches/JobTracker/native-build/debug/JobTracker
```

`JOBTRACKER_NATIVE_SUPPORT_DIR` relocates the pending-request journal and
`JOBTRACKER_LOCK_DIR` the store lock, for tests. Packaged builds read
`JTEngineMode`/`JTStoreFolder` from Info.plist and run the helper in
`Contents/Resources/engine/` (built by `scripts/build_native.py`).

`JOBTRACKER_NATIVE_CAPTURE=<dir>` renders every named theme and page (both
graph modes; `JOBTRACKER_NATIVE_CAPTURE_PANELS=1` adds panels) to PNG files for
visual review, then exits. Use it only with synthetic stores.

## Toolchain limits

The Command Line Tools compile SwiftUI but ship no macro plugins for
`@State`, swift-testing or XCTest UI automation. The app therefore keeps
view-local state in `ViewState` objects, and checks run as an executable.
Native UI automation (XCUIAutomation) and VoiceOver passes need full Xcode and
an interactive session; they are recorded as manual checks in the
[evidence record](../../docs/guides/a4-evidence.md).
