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
XCUIAutomation requires full Xcode. The release instead combines the executable
checks, real-window macOS input/accessibility automation and the user's pilot
review. Test methods and limits are recorded in the
[evidence record](../../docs/guides/a4-evidence.md).

## Review corrections

Native replay responses retain the original refusal/missing outcome. Mutation
captures are immutable while unresolved; another save waits for confirmation
instead of replacing its payload or continuation. New journal entries also bind
to the canonical store path, so a copied device ID cannot redirect their replay.
Legacy entries without a path retain compatibility and must be resolved against
their original pilot store before moving or copying that store.

Late confirmations update the original editor's entity identity and preserve
newer field input. History refresh retains explicitly selected sessions outside
the first page and keeps unsaved text if a target disappears. Date fields reject
invalid/zoned input and preserve naive wall-clock fields across DST; duration
validation remains with the Python engine. Close/Escape cannot discard a save
in progress, and an unconfirmed quit is not treated as successful.

Ordinary delayed session saves use the same draft rebasing as late replies:
newly typed fields remain edits of the confirmed session, rather than creating
another row. The native checks also run two separate engine stores against a
disposable loopback API, exercising remote moves during an open session draft,
explicit conflict resolution, settled queues and foreign timer ownership.
Forms that close after Save disable their fields while that save is pending;
the session panel instead keeps newer edits bound to the confirmed row.

## Native interaction refinements

Subjects and Goals lift under a mouse drag and preview the insertion gap. Only
release commits the final order; Escape or a drop outside the list cancels.
The lifted card travels to its slot before an animation-free handoff to the
row. Drag gestures take priority over child buttons so a drag across Complete
cannot also complete a goal. Ordinary button clicks retain their actions.
Starred active goals appear first, preserving the existing order within each
group; dragging stays within a focus group. Completed goals keep free ordering.
Session panels accept Up/Down to select adjacent history rows and scroll them
into view. Text fields and open menus keep their keys; dirty drafts still ask
before changing selection.

Bar totals use an 11-point shared base, scaled by intensity. Every non-empty
bucket keeps its total directly above its own bar, with the original tier glow.
A dense chart scrolls horizontally to preserve readable spacing. Agenda blocks
with room for two lines retain their duration; a displaced start time yields
to the duration when the column is narrow. Exact saved times remain unchanged.
Tall, narrow fortnight blocks use compact durations such as `2h` or `90m`
with tighter padding when there is no room for the subject name.

The Weight plot accepts pinch zoom and two-finger panning. Shift-pinch zooms
time alone; Option-pinch zooms weight alone. Shift-scroll and Option-scroll
stretch the corresponding axes. Reset zoom restores the loaded range. Zoom is
temporary presentation state, stays through fullscreen, and resets when the
range or historic-reading option changes. It never changes measurement rows,
the entry draft, smoothing, or persisted settings.

The Tangent toggle replaces hover inspection and click-to-edit with a tangent
to the trend at the pointer's date, labelled with the implied kcal/day and
kg/week (`Charts/WeightTangent.swift`; constants in
[the weight task map](../../docs/features/weight.md#charts-and-fullscreen)).

## Subject colours and session editing

New subjects offer four suggestions above the eight preset swatches and Custom.
The engine greedily maximizes the nearest Oklab distance to active subject
colours and earlier picks, using a bounded colourful palette. Archived colours
are excluded; the first suggestion is the initial choice. Existing custom
colours are preserved exactly. The pure algorithm lives in
[`colors.py`](../../jobtracker/core/colors.py).

Subject history, graph-day history and agenda clicks open one session panel.
The history rows contain time, duration and subject; repeated selection/day
summaries and Quick Add presets are absent. Subject changes save immediately;
start/end edits save on Return or editing completion. Invalid input stays visible
for correction, and Save remains an explicit retry. Closing a dirty editor offers
to keep editing or explicitly discard; pending writes keep the editor open. New Session in subject
history keeps manual entry available. Moving a selected row out of the current
scope clears selection, so another tap cannot modify a different row.

Move presets shift both endpoints by ±15 minutes, ±1 hour or ±1 day. Duration
presets (±5 minutes, ±15 minutes, ±1 hour) keep the start fixed and adjust only
the end. Reductions below 30 seconds are refused before mutation. Duplicate
to Today and +1 Day keep the existing logical-day rules.

Saved agenda blocks lift under a left-button drag. Cards and blocks share lift
geometry, a five-point threshold and Escape cancellation; Subjects and Goals
share the complete reorder component. Agenda drops snap the relative vertical
movement to five-minute steps and choose the destination logical-day column.
The exact recorded seconds and microseconds, subject, duration and UID survive.
Arranged overlapping blocks use their displayed position as the grab origin;
only the dragged session changes. An outside drop or Escape writes nothing.
Running blocks are read-only; an ordinary click still opens the exact session.
Graph legends are omitted because labels and hover inspection identify subjects.

The engine owns all day/time arithmetic. Each edit/move/resize captures expected
endpoints and subject; a stale capture is refused instead of overwriting a change
from another device. Writes use the ordinary service overrides, atomic outbox
and local replay receipts. One unresolved command blocks later mutations.
