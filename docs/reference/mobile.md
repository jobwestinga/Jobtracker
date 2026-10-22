# Phone implementation details

Read this reference when the task touches these owners. Start with the shared
[engineering rules](../engineering-rules.md) and [task index](../index.md).

## The phone app (`web/`)

Plain HTML/CSS/ES modules — no framework, no bundler, no npm. Deploying it is
the same rsync that ships the server, and changing a colour needs nothing
installed. Served by the API itself; static files need no token, the data behind
them does.

- Authoritative rules stay in shared services. `/api/context` supplies the
  logical day/boundary; server queries supply graph totals. Client date helpers
  and optimistic projections support offline display and captured user input,
  not an alternate milestone gate, timer policy or aggregation rule. Reconcile
  provisional state with the canonical server answer.
- Writes go through the same idempotent operations, with a `localStorage` outbox
  when there is no signal. Anything already queued is sent first, so a delete can
  never overtake the create it refers to.
- Every action paints optimistically before the request goes out (a tap that
  waited a round trip felt broken), then the refresh that follows replaces the
  guess with the server's answer.
- Explicit profiles in [profile.js](../../web/shared/profile.js):
  `/phone/weight/` has Weight only, `/phone/jobtracker/` has Goals, Subjects,
  Sessions and Graphs and starts on Subjects. Browser `/` retains all five
  sections and starts on Weight. Width/user agent never selects a product.
  Standalone legacy root launches redirect to Weight without moving storage.
- A fresh Weight launch starts on entry; foreground resume preserves the current
  tab and draft. A durable weight save clears its picker unless a newer draft
  was entered while saving. Fullscreen plots keep their DOM and resize on rotation.
- **The app is a fixed frame**: `#app { position: fixed; inset: 0 }`, flex
  column, `#views` scrolling in the middle, tab bar as a flex item. Not
  `height: 100dvh` — in an iOS standalone app dvh resolves before the viewport
  settles, so the frame came up short and left a strip of page background below
  the tab bar, which then "fixed itself" on rotation. `inset: 0` is always the
  real viewport. Nothing in the layout may depend on page height.
- **Opaque status bar** (`apple-mobile-web-app-status-bar-style: black`). With
  `black-translucent`, iOS 26+ standalone apps report a viewport one status-bar
  height short (797 of 844 px on a 390-wide iPhone) and paint nothing below it,
  leaving a band at the bottom. Content just under the status bar is also
  blurred. Neither CSS heights nor measured JS heights can paint into that band.
  Installed apps may keep the style they were added with, so re-add them after
  changing it. Header and tab icons are inline `currentColor` SVGs, not emoji.
- **Subjects follow the Mac's order** (`sort_order`, then newest first) through
  `orderSubjects` in [order-controls.js](../../web/shared/order-controls.js);
  snapshots arrive in database order. Subject cards take the Mac styling: a
  subject-colour frame with a solid left edge. Agenda blocks use the selected
  **Bold** finish: a strong subject tint with an integrated subject edge, bold
  title and duration line. `layoutAgendaDay` in
  [agenda-time.js](../../web/features/graphs/agenda-time.js) joins same-lane
  neighbours separated by at most max(30 s, one device pixel) into one shape
  with no divider, and places overlapping sessions in side-by-side lanes at
  their true times. Each block keeps its own session target.
- **Subject colours** use one compact set: the eight shared hues from
  `design/ui_roles.json`, the current colour when it is custom, and a Custom
  control. An untouched saved colour is resubmitted exactly as stored.
- **Graphs show only view and range**; bar grouping and agenda hours are in the
  options sheet. Fullscreen is a fixed column whose chart fills the remaining
  height, so the header never scrolls over the controls.
- No `backdrop-filter` on the chrome. Nothing scrolls under the bars any more,
  so it was a GPU composite every frame for a blur of a solid colour — and the
  translucency is what made the bottom look two-toned.
- **Bars are summed per SUBJECT before drawing.** The API returns one segment per
  *session*. Summing first avoids a minimum block height inflating long buckets
  until every bar reaches the ceiling.
- Quick add matches the Mac: **15m, 30m, 1h, 1h 15m, 1h 30m and 2h**, with
  custom duration chunks below. Presets stay visible in the add-time sheet;
  whole-session move presets stay visible in the session editor.
- Filled stacks use the bucket-to-peak height, with each subject occupying its
  share of that bucket. Rounded caps and gentle shading leave segments
  contiguous. Hour labels have no boxes, grow continuously with daily intensity
  (capped at 14h) and use
  one soft colour bloom (R1) behind crisp text for achievement tiers,
  strongest above 10 hours per day. Bloom radii and tier colours come from the
  generated role block in [tokens.css](../../web/styles/tokens.css).
  Dense columns scroll within the chart and text scaling stays available.
  Hour labels retain daily intensity tiers; the API supplies the
  normalized intensity for weekly/monthly buckets. Up/Down on graph mode
  buttons selects Stacked Bar/Agenda and keeps focus on the selected control.
- Poll while foregrounded, never while a sheet is open. Preserve drafts and
  existing interactive DOM during refreshes; timer-only updates must not rebuild
  a form or chart.
- The minute poll asks `/api/context`. Changed feed heads update the mirror;
  unchanged data only updates elapsed time. Use an initial/repair snapshot when
  needed, and load weight lazily through `/api/weight`. Keep response compression
  enabled rather than increasing repeated full-history requests.
- `sw.js` makes it open with no signal. **Network-first everywhere**, falling
  back to the cache: the app is deployed by rsync and reloaded by hand, so a
  cache-first shell would keep serving yesterday's JavaScript after a deploy.
  Bump `VERSION` in `sw.js` when the shell changes, and keep `SHELL` complete
  for every transitive local import and stylesheet. The worker caches shell assets
  only, never API responses, and never replays writes. The app's own outbox owns
  retry identity and order; keep replay in one place.

## Profiles, installation and storage

[The phone guide](../guides/iphone-apps.md) covers installation and clean
reinstalls. One root-scoped worker caches the complete shell and both explicit
app shells. Fallbacks match the requested path, including navigation queries;
they never substitute another product. Updates remove old shell caches only,
never credentials, mirrors or queued writes. First setup requires connectivity.

Weight retains manifest `id: /`, scope `/`, and all legacy `jobtracker.*` keys,
including queued time operations. JobTracker has `id`/scope/start URL
`/phone/jobtracker/` and `jobtracker.phone.jobtracker.*` keys. This isolates tokens,
queue ownership, drain locks, caches and preferences even with shared storage.
iOS may isolate installations instead; each can enroll independently. Tabs and
unsaved drafts live in each page, not a shared last-view key. Navigation is a UI
boundary, not authorization or another account.

Weight skips loading the tracking snapshot/mirror, but drains every legacy
operation before reading context and weight history. The full/JobTracker views
keep incremental mirror refresh. No service, API or database semantics change;
the native app is unaffected.

Edit [web/index.html](../../web/index.html) and profile metadata, then run
`python3 scripts/generate_phone_apps.py --write` (or `dev.py check --write`).
Generated static shells/manifests are committed and deployed without a build.
`dev.py check` verifies drift and their transitive offline assets. Root deployment
is supported; reverse-proxy prefixes are not. Keep origin-root asset/API URLs
until the whole deployment stack explicitly supports prefixes.
