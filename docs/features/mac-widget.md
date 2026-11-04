# Mac desktop goals widget

The **Goals** widget is a medium desktop widget with up to four active goals.
Add it through **right-click desktop → Edit Widgets → JobTracker → Goals**
after launching the updated Mac app once. Its size matches other medium system
widgets. Remove or reposition it through the normal macOS widget controls.

## What appears

1. Starred weekly-focus goals, preserving their order in the app.
2. Remaining goals with valid deadlines, earliest first, including overdue goals.
3. Remaining goals, most recently created first; legacy missing/invalid creation
   times follow valid timestamps. Ties preserve the app's order.

Completed goals are excluded. A deadline or milestone count appears beside each
title when available, and the footer identifies when the Mac published the data.
Titles longer than 500 characters are shortened with an ellipsis in the display
cache so imported goals cannot prevent widget updates. The full saved title and
the goal's identity are preserved when opening it in the app.
This selection never changes your saved order, focus, recurrence or completion.
Older unfinished goals remain in the app even when outside these four rows.

The widget is read-only. Click a row to open that exact goal in JobTracker, or
click its background to open Goals. The app accepts only its own goal-navigation
URL routes; a stale link to a deleted goal explains that it is unavailable.
An existing editor or draft is retained when navigating from the widget.

## Offline use and freshness

The native app publishes a small local display cache after startup and after
committed goal/sync changes. The widget can keep showing it offline or when the
app is fully quit. Phone changes and new recurring goals appear after the Mac
engine next runs and synchronizes/generates them. WidgetKit decides the exact
refresh time; a reload request is not a guarantee of an immediate redraw.
An unavailable/invalid cache shows **Open JobTracker**; a valid empty cache shows
**No active goals**. Failed refreshes never erase the last good file.

The snapshot contains only the four goal UIDs, titles, focus flags, display
labels and milestone counts, plus an active count and update metadata. No notes,
credentials or server addresses are shared. Production and pilot caches are
separate; isolated source/test runs do not publish into the production cache.

## Implementation and checks

Run `python3 scripts/dev.py context native.widget` for exact owners and checks.
The pure selection is in `jobtracker/core/goal_widget.py`; the local engine's
`widget.goals` and `goal.get` commands remain reads. The Swift app publishes
validated snapshots atomically. `JobTrackerWidgetSupport` owns the bounded cache
codec, safe goal routes and medium layout. The extension reads one cache file
through a narrowly scoped sandbox entitlement; it has no database, timer,
network or completion-writing path.

Python tests cover ranking, stable ties, malformed legacy dates, read-only
behaviour, filtered payloads and preservation of widget entitlements through a
real nested-bundle reseal. Native checks cover cache replacement/failure,
production/pilot isolation, the Completed filter, goal links on cold launch,
removed goals, draft retention and four-row/long-title/empty layouts. Set
`JOBTRACKER_WIDGET_CAPTURE_DIR` to a private output directory when running the
native Widget checks to inspect synthetic layout captures.

The macOS extension must link through Foundation's `_NSExtensionMain` XPC
bootstrap while retaining Swift's `@main` and `__swift5_entry` metadata for
WidgetKit. The Swift wrapper's exported symbol name varies across toolchains;
validation checks nonempty entry metadata and executable code rather than
requiring a global `_main` symbol. Starting the Swift widget as an ordinary
executable crashes during extension initialization and leaves it absent from the gallery, even when
PlugInKit registration and signature checks succeed. Packaging verifies both
entries and requires a direct launch to refuse with **An XPC Service cannot be
run directly.** Real-product packaging tests cover ordinary and private Swift
entry symbols and reject both a plain executable and an XPC bootstrap without
Swift entry metadata.

That bootstrap check and PlugInKit registration still do not prove gallery
availability. After installing a widget packaging change, open **Edit Widgets**,
search for **JobTracker**, and confirm the medium Goals preview appears. Check
local `chronod`/`JobTrackerGoalsWidget` logs if descriptor loading fails; do not
clear widget preferences or restart system processes to mask a startup defect.

Local macOS packaging uses the installed SDK and ad-hoc signing; it does not
require a new Apple account. The extension is discovered by macOS PlugInKit;
WidgetKit renders and schedules it. See Apple's [widget extension guide](https://developer.apple.com/documentation/widgetkit/creating-a-widget-extension),
[refresh guidance](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date)
and [documented sandbox file permissions](https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/EntitlementKeyReference/Chapters/AppSandboxTemporaryExceptionEntitlements.html).
