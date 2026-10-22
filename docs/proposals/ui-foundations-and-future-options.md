# UI foundations and future options

Status: proposals for review, 22 October 2026. These are choices, not accepted
implementation requirements. The separate Visual options v2 PDF remains the
reference for Bold/Soft agenda finishes and existing theme previews. This
companion adds composition, workflow, rendering and architecture choices.

## Choices at a glance

Effort is relative implementation scope, not a time estimate. Visual benefits
and maintenance benefits are design judgments to validate in a prototype.

| ID | Concrete change | Relative effort | Relationship |
| --- | --- | --- | --- |
| F1 | Shared typography, spacing, surfaces and component roles | Medium | Useful with every direction; recommended first |
| F2 | Refresh only affected views and preserve presentation state | Medium | Complements F1; extends existing reuse |
| S1 | Familiar subject cards with quiet, visible text actions | Small after F1 | Alternative to S2 |
| S2 | Aligned subject rows with a duration column | Medium after F1 | Alternative to S1 |
| W1 | One consistent session panel across entry points | Small/medium | Builds on the existing shared actions |
| W2 | Dock that same session panel beside history on wide screens | Medium | Alternative wide-screen presentation of W1 |
| R1 | Soft cached bloom, crisp text and stable focus geometry | Small/medium | Refines the v2 hour treatment; preserves its intent |
| A1 | Evolve the current Qt Widgets frontend | Medium overall | Recommended delivery route |
| A2 | Replace only the graph workspace with Qt Quick/QML | Medium/large | Optional rendering experiment |
| A3 | Share HTML/CSS presentation between Mac and phone | Large | Alternative long-term frontend direction |
| A4 | Build a SwiftUI/AppKit Mac frontend | Very large | Alternative to A2/A3; phone remains web-based |

## F1: one visual contract

Extend the existing theme tokens with named typography, spacing, border,
elevation and action roles. A small UI-owned source can generate checked-in CSS
values and supply the desktop stylesheet and painter helpers. No runtime web
bundler, downloaded font or hosted design service is required.

Use a verified system font for desktop widgets and painted text, with an
explicit size convention. The research probe resolved the current widget font
stack to Helvetica Neue and the chart's requested SF Pro Text to Apple's
system font. Consolidating that decision also consolidates measurement,
baseline and elision behavior. Choose a small hierarchy of body, secondary,
heading and numeric styles; preserve magnitude-scaled chart totals as a
deliberate exception.

Create shared Primary, Secondary, Quiet and Destructive action components.
Normal, hover, keyboard-focus, pressed, disabled and selected states should have
stable geometry. Keep subject identity in its marker and chart fills; ordinary
Edit/Sessions/Archive actions can stay visible without three coloured frames.

Start with a synthetic reference gallery for buttons, fields, subject rows,
goal rows and chart labels at 1x/2x and narrow/wide widths. Review it together
before rolling it out. Automate high-value focus/reflow regressions; avoid
snapshotting every low-impact styling value.

Owners: [theme tokens](../../jobtracker/core/themes.py),
[desktop stylesheet](../../jobtracker/ui/styles.py),
[subject cards](../../jobtracker/ui/widgets/subject_item.py),
[phone styles](../../web/styles).

## F2: targeted updates and explicit presentation state

Extend existing card reuse and phone feed-head checks. Add a presentation update
coordinator at the existing MainWindow/controller boundary with narrow topics
such as subjects, goals, sessions, weight and theme. Batch committed results into
one update per affected view. A timer tick should update the clock and live plot
overlay, not recreate forms, controls or the history list.

Keep selected UID, focused control, scroll position, filters and unfinished
drafts in an explicit feature presentation state. Restore them by identity
after a refresh. Cache chart layout and bloom by data revision, viewport,
theme and display scale; invalidate the relevant cache when one changes.

Publish UI invalidations only after the outer durable transaction succeeds,
including a queue-aware sync mutation. Preserve facade imports, return values
and override dispatch. Do not put Qt signals in core/services or invent a second
timer, connection, transaction owner or event-sourcing system.

Owners: [MainWindow](../../jobtracker/ui/app.py),
[sync controller](../../jobtracker/ui/controllers/sync.py),
[reorderable list](../../jobtracker/ui/widgets/reorderable_list.py),
[phone composition](../../web/app.js).

## S1 / S2: choose the subject composition

**S1 - Calm cards.** Keep the current order, card interaction and colour marker.
Use one quiet border, a shared text baseline and visible neutral text actions.
Use the strong accent for the active page and primary action. The familiar
layout changes less, but remains relatively spacious.

**S2 - Aligned rows.** Replace separate cards with rows under Subject, Tracked
and Actions columns. Use tabular numerals and a stable duration column; keep
reordering and the subject's colour marker. This makes long lists easier to
scan, but feels more like a workspace. At narrow widths, secondary content can
reflow beneath the subject while actions remain reachable.

These are alternative compositions, not two new settings to maintain forever.
Both retain the last-tracked Resume action, arrow navigation and Goals-only
number shortcuts. Neither adds a KPI strip or reconnects Goals to Subjects.

## W1 / W2: one session panel, two presentations

The repository already shares session rows and mutation actions. Reuse those
owners. W1 would consolidate the presentation around a stable selected-session
header, the exact editor fields, and the same always-visible duration/custom
and move controls from every applicable entry point. Quick add targets the
explicitly displayed subject; moving targets the selected session UID.

W2 would dock this panel beside history at sufficiently wide desktop widths.
Selecting another row updates the same panel and keeps surrounding history
visible. On a narrow Mac window or phone, use the inline panel/sheet with the
same field and action components. Do not implement a second editor or duplicate
the write path. An unfinished draft needs an explicit keep/discard decision
before switching edit targets.

Keep the commands distinct: Quick add creates a new session; Save submits the
field draft for the selected UID; Move shifts that UID's whole interval and
resolves a pending field draft first. Preserve the existing duplicate-submit
guards and failure-draft behavior. A generic Save must not silently bundle
these separate mutations.

The sketches include 15m, 30m, 1h, 1h 15m, 1h 30m, 2h and Custom, with move
controls exposed. The adjacent agenda examples use non-overlapping intervals.
Whole-block movement uses the existing shift action and preserves duration.

Owners: [shared session actions](../../jobtracker/ui/widgets/session_list.py),
[session editor](../../jobtracker/ui/widgets/session_dialog.py),
[history panel](../../jobtracker/ui/widgets/manage_sessions_dialog.py),
[phone sessions](../../web/features/sessions/index.js).

## R1: improve rendering without changing the chart's meaning

Keep v2's unboxed totals, continuously scaled sizes, intensity colours and red
emphasis above 10h. Replace the current stroked glyph halos with a small blurred
colour mask beneath normal, crisp text drawing. Cache that mask by label/font/colour/display
scale. The companion PDF contains an offscreen Qt prototype of this technique;
it is not an installed renderer change.

Use consistent physical-pixel alignment for thin rules and icon strokes. Keep
focus emphasis outside the content geometry or reserve its space, so changing
a border from normal to focused does not move the label. Introduce a shared
chart scene contract and synthetic cases for totals, labels, colours and hit
identities across the two clients. Platform renderers can differ; timestamp,
logical-day and aggregation rules stay with their existing owners. Never send
server-generated pixel geometry as a requirement for offline rendering.

Owners: [time bars](../../jobtracker/ui/widgets/graphs_view.py),
[agenda](../../jobtracker/ui/widgets/agenda_view.py),
[phone graphs](../../web/features/graphs/index.js).

## A1-A4: how far to change the frontend

### A1 - Evolve Qt Widgets (recommended first)

Deliver F1/F2 and a selected composition within the current frontend. Extract
shared visual primitives and a feature presentation state around existing
MainWindow adapters. Core/services stay Qt-free. This offers the smallest
migration and preserves the established Mac fullscreen/dialog behavior. Mac
and phone still have separate renderers and require parity checks.

### A2 - Qt Quick workspace

Prototype only Graphs/Agenda in QML, hosted by the existing widget shell. Qt
Quick's retained scene graph provides different tools for compositing and
motion, but it is not automatically faster: QQuickWidget adds an offscreen
render pass and disables the threaded render loop. Measure the actual embedded
prototype, including its memory, focus, inline overlays and fullscreen behavior.
Keep a UI-only bridge to the existing services and preserve exact hit UIDs.
Phone rendering still needs a separate implementation.

### A3 - Shared web presentation

Reuse and extend the existing plain ES-module UI and common visual components
for phone and a local Mac web view. Keep explicit platform adapters: on desktop,
commands go through the existing Python service/outbox owner; on phone, the
current durable operation queue talks to the user's server. The desktop must
not enqueue a second copy in browser storage or require its server to be up.

Qt WebEngine/WebChannel is one bridge candidate. Load bundled, trusted local
assets and expose a narrow command surface. Compare installed size, startup,
memory, native menus, keyboard focus and file workflows before choosing it.
Shared screens reduce presentation duplication but do not eliminate
platform-specific behavior. This is an optional architecture change, not a
proposal to add a framework, telemetry or external hosting.

### A4 - Native Mac frontend

Build SwiftUI/AppKit views over a narrow local command bridge to a Python
service owner. This creates access to native Mac controls and interaction
patterns. It also introduces a new bridge, packaging and lifecycle work while
the phone remains a separate web UI. Python must retain sole ownership of the
timer, SQLite connection and durable queue; the native frontend must not write
the database directly. Keep `python main.py` available during an incremental
transition. Prove a read-only Subjects screen and one sandboxed edit flow first.

A2, A3 and A4 are alternative experiments. The screen sketches do not promise
that changing the toolkit will produce the desired aesthetic by itself.

## Suggested packages and order

| Package | Pick | Purpose |
| --- | --- | --- |
| Familiar polish | F1 + R1 + S1 + W1 on A1 | Improve consistency with the least workflow change |
| Focused workspace | F1 + F2 + R1 + S2 + W2 on A1 | Improve scanning, selection and editing |
| Frontend experiment | F1, then one A2/A3/A4 prototype | Learn whether a different presentation stack earns its migration cost |

Start with the reference gallery and one Subjects screen. Choose S1 or S2 from
that rendered screen, then apply the selected rules to Goals, Weight and
session editing. Pick either v2 agenda finish independently. If the result
still needs a different rendering stack, compare one working view in a single
alternative before committing to a larger migration.

A useful reply can name choices directly, for example:
`F1 + F2 + S2 + W2 + R1; keep A1; v2 Soft agenda.`

## Source references

These are observed implementation patterns, not measurements of JobTracker's
future performance. All proposed screen designs use synthetic content.

- [AppFlowy theme data](https://github.com/AppFlowy-IO/AppFlowy/blob/main/frontend/appflowy_flutter/packages/appflowy_ui/lib/src/theme/definition/theme_data.dart): separate text, spacing, radii, surface, icon and shadow roles.
- [Grafana typography](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/themes/createTypography.ts): coordinated size/line-height variants.
- [Grafana axis builder](https://github.com/grafana/grafana/blob/main/packages/grafana-ui/src/components/uPlot/config/UPlotAxisBuilder.ts): theme fonts, measured labels and device-pixel-aware strokes.
- [VS Code button](https://github.com/microsoft/vscode/blob/main/src/vs/base/browser/ui/button/button.ts): reusable primary/secondary styles and interaction state handling.
- [Proton Calendar event styles](https://github.com/ProtonMail/WebClients/blob/main/applications/calendar/src/app/styles/_eventcell.scss): coordinated adaptations for short event cells.
- [Qt QQuickWidget performance](https://doc.qt.io/qt-6/qquickwidget.html#performance-considerations): additional render pass and threaded-loop limitation.
- [Qt scene graph](https://doc.qt.io/qt-6/qtquick-visualcanvas-scenegraph.html): retained graphics architecture.
- [Qt WebChannel](https://doc.qt.io/qt-6/qtwebchannel-index.html): host/JavaScript communication.
- [Apple SwiftUI](https://developer.apple.com/documentation/swiftui): native frontend framework reference; the Python bridge above is a proposed design.
