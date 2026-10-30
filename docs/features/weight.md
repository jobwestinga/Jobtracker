# Weight task map

Start here for weight work; use [the feature index](../generated/feature-index.md)
for another domain. Weight is independent of tracking Subjects and Goals and
uses calendar dates. The [feature contract](../reference/features.md#feature-owners-and-stable-facades)
defines daily entry, draft and precision rules.

## Entry and reset

`python3 scripts/dev.py context weight.daily-entry` prints the checked owners.

| Responsibility | Owner |
| --- | --- |
| Phone DOM, draft revision, save receipt and reset | [index.js](../../web/features/weight/index.js), `initialize`, `paintPicker`, `save` |
| Whole/decimal choices, latest anchor and date collision | [picker.js](../../web/features/weight/picker.js) |
| Optimistic weight/alias projection | [projection.js](../../web/features/weight/projection.js) |
| Native entry and service injection | [WeightView](../../jobtracker/ui/widgets/weight_view.py) |
| Validation and canonical daily identity | [WeightService](../../jobtracker/services/weight_service.py), [repository](../../jobtracker/core/persistence/weights.py) |
| API/read adapter | [weight route](../../server/routes/weight.py) |

The refresh-shaped button beside Save clears both tile selections; it does not
delete a saved measurement or change an explicit edit/date. It advances the
draft revision, so an older in-flight save cannot clear a newer selection.
Durable online/queued saves still clear only their unchanged captured draft.

## Charts and fullscreen

`python3 scripts/dev.py context weight.plot` prints plot owners and checks.

| Responsibility | Owner |
| --- | --- |
| Whole-history fit, view window, zoom gestures, inspection | [index.js](../../web/features/weight/index.js), `chartView`, `bindChartGestures` |
| Canvas drawing and year labels | [chart.js](../../web/features/weight/chart.js), `drawWeightChart` |
| Calendar coordinates, January 1 boundaries, LOESS/PCHIP | [math.js](../../web/features/weight/math.js), [weight_plot.py](../../jobtracker/core/weight_plot.py) |
| Native drawing, theme colours, hover/click targets | [WeightChart](../../jobtracker/ui/widgets/weight_chart.py); compatibility import remains in `weight_view.py` |
| Fullscreen lifecycle, fallback, focus/scroll restoration | [shared/fullscreen.js](../../web/shared/fullscreen.js), [shared CSS](../../web/shared/fullscreen.css) |
| Plot-only fullscreen, picker and chart layout | [weight styles](../../web/features/weight/styles.css) |

The trend is fitted once over the whole history (`trendFor`) and each view shows
its stretch (`trendBetween`), so a smoothing preset keeps the same neighbourhood at
every range; fitting only the visible points made a 90-day LOESS trace every
weighing. Plot options open from the toolbar button; Recent weighings lists the
readings. Fullscreen shows the chart, exit button and the inspected weighing:
pinch zooms the time axis (at least 7 days), one finger pans across the whole
history, double tap or **Reset zoom** restores the chosen range, and `+`/`-`/`0`
do the same from a keyboard. Leaving fullscreen or changing the range resets the
zoom. Options, custom range and help remain in the DOM and return on exit.
Rotation repaints the same canvas; unchanged data reuses its fitted trend. January 1 lines use calendar
coordinates, with years on a separate label row. Dense histories keep every
year line but thin labels to prevent overlap. Native dots are neutral and the
theme accent trend is drawn above them. The phone canvas uses the matching dark
`screen` palette. **Save plot** draws the original light `print` figure
off-screen at the plot's current size.

**Tangent** (a phone plot option, a native toggle) replaces reading inspection
with a dashed tangent touching the trend at the pointer's date: mouse hover on the
Mac or in a desktop browser, tap or horizontal drag on the phone (one finger in
fullscreen; two fingers still pinch and pan). Its slope is a central difference
over ±3 days of the trend (±2% of the visible span when zoomed in, at least half a
day), shown as kcal/day and kg/week. Losses use 7,700 kcal/kg (adipose tissue);
gains use 5,600 kcal/kg, assuming roughly half lean tissue (~3,500 kcal/kg with
synthesis cost) during hypertrophy training. The constants live in
`trendTangent`/`tangentEnergy` ([math.js](../../web/features/weight/math.js)) and
`WeightTangent` (native). The native toggle is presentation state and is not saved;
the phone keeps it with its other plot preferences.

The native plot's Fullscreen button expands it within the existing Mac window,
temporarily hiding the sidebar, picker and history. Escape or page navigation
restores them without clearing the draft. Show dots remains a local preference;
dense histories use smaller dots behind the trend line.

The native `30d` badge compares the first and last eligible readings within the
latest reading's trailing 30 calendar days. Its tooltip gives the actual dates;
it stays hidden with fewer than two readings and respects Historic readings.
The summary separately labels the visible range's change. History row changes
compare consecutive readings, retaining hundredth-kilogram differences.

The shared Python LOESS kernel retains every fitted point and the same tricube
weights while using direct cubic multiplication. The
[long-history measurement](../guides/a4-evidence.md#local-long-history-weight-fitting-25-october-2026)
records the command boundary, synthetic data, repeated results and limits.

## Phone shell and home screen

`python3 scripts/dev.py context mobile.shell` covers navigation and caching.
[shell.css](../../web/styles/shell.css) owns the bottom bar; preserve the fixed
app frame and home-indicator clearance. [The manifest](../../web/manifest.webmanifest)
allows either orientation. New imports/styles must enter
[sw.js](../../web/sw.js)'s shell inventory with a version bump.

The phone client is an installable web app, with an offline shell and durable
outbox. [The phone apps guide](../guides/iphone-apps.md) explains separate Weight
and JobTracker identities. Icon artwork lives in [web/icons](../../web/icons/weight.svg);
The [installation guide](../../web/phone/index.html) is served at `/phone/` for
reinstalls. Weight owns the root `/`
identity; JobTracker uses `/phone/jobtracker/`. Metadata is owned by
[profile.js](../../web/shared/profile.js); [the generator](../../scripts/generate_phone_apps.py)
shares one HTML shell. Preserve the root standalone redirect and queued writes.

## Verification

- Entry, stale responses, offline durability and rotation:
  [browser.cjs](../../tests/mobile/browser.cjs).
- Year/date boundaries and canvas axes:
  [weight-axis.test.mjs](../../tests/mobile/weight-axis.test.mjs),
  [native plot checks](../../tests/test_native_weight_plot.py).
- Native entry, themes and hit testing:
  [native weight UI checks](../../tests/test_native_weight_ui.py).
- Focus, keyboard and reflow:
  [accessibility.cjs](../../tests/mobile/accessibility.cjs).

Use the [testing runbook](../runbooks/testing.md) for isolated environments and
the full suite; use [deployment](../runbooks/deploy.md) and
[build/install](../runbooks/build-install.md) to deliver verified changes.
