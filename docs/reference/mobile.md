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
- Tab order: Goals, Subjects, Sessions, Graphs, Weight.
- A fresh phone launch starts on Weight; foreground resume preserves the current
  tab and draft. A durable weight save clears its picker unless a newer draft
  was entered while saving. Fullscreen plots keep their DOM and resize on rotation.
- **The app is a fixed frame**: `#app { position: fixed; inset: 0 }`, flex
  column, `#views` scrolling in the middle, tab bar as a flex item. Not
  `height: 100dvh` — in an iOS standalone app dvh resolves before the viewport
  settles, so the frame came up short and left a strip of page background below
  the tab bar, which then "fixed itself" on rotation. `inset: 0` is always the
  real viewport. Nothing in the layout may depend on page height.
- No `backdrop-filter` on the chrome. Nothing scrolls under the bars any more,
  so it was a GPU composite every frame for a blur of a solid colour — and the
  translucency is what made the bottom look two-toned.
- **Bars are summed per SUBJECT before drawing.** The API returns one segment per
  *session*. Summing first avoids a minimum block height inflating long buckets
  until every bar reaches the ceiling.
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
