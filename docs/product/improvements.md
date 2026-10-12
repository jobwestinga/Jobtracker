# App improvements

Reviewed 11–12 October 2026 against the current Mac and phone implementations.
These are follow-up ideas, not promises that the features below already exist.
Keep daily entry fast; put infrequent controls in sheets or expandable sections.

## Fixes from this review

- Protect phone goal, template, settings and session sheets against repeated
  submissions. Retain their exact drafts when saving fails before persistence.
- Make settings faithfully display and save a day boundary such as `03:30`.
  Report queue and disconnect failures instead of silently losing controls.
- Validate phone acknowledgements as an ordered prefix; reject stale success
  and failure responses after credentials change. Keep pending work intact.
- Bind queued phone changes to their connection before sending, including when
  another browser tab changes the token. Retry the original operation identities.
- Scope the phone tracking mirror to its credential, as weight caches already
  are. Unverified legacy read caches refresh from the server; the durable queue
  is unchanged. Prevent changing a saved token while its changes are waiting.
- Preserve fractional weight timestamps and existing deterministic import IDs.
  Keep queued edits/deletions visible when the server coalesces daily weight IDs.
- Fix native dialog cancellation, archived-subject editing and stale timer
  confirmations. Update the active timer label immediately after a subject edit.
- Retain and reuse native feedback animations, avoiding crashes when Python
  garbage collection overlaps Qt cleanup; repeated feedback stays bounded.
- Make new `web/data` source visible to Git while private root `data` stays
  ignored. Regression checks cover every public phone runtime asset.
- Export related backup tables from one read snapshot, so concurrent sync
  cannot produce a backup assembled from inconsistent database states.

The [feature index](../generated/feature-index.md) links owners and regression
tests. The [write contracts](../architecture/write-path.md) explain durability,
receipts and identity; the [time model](../architecture/time-model.md) explains
tracking days versus calendar weight days.

## Selected work

The selected work is implemented:

- Persistent phone and Mac header status shows pending changes, confirmed sync
  time and connection problems. Status bookkeeping cannot interrupt a save.
  On the phone the status opens the detailed Settings view; a sync from an
  earlier day shows its date. The Mac pushes newly queued local edits after a
  short debounce instead of waiting for focus or the five-minute timer.
- Phone Subjects offers Add time directly on each active subject, using the
  existing presets and additive custom duration controls without time fields.
  A saved quick add confirms its range and, before the day boundary, the
  tracking day it counts towards. Subject sheets use the shared durable action
  guard, and a double tap on a subject cannot start and immediately stop it.
- The accessibility pass improves keyboard focus, sheet/fullscreen navigation,
  labels, touch controls, larger text and reduced motion. Weight plots expose
  expandable data tables; compact Mac Settings scrolls to focused controls.

Search was declined and removed from this backlog. Of the saved-day feedback
idea, only the quick-add receipt above was implemented. Actual iPhone VoiceOver and Safari checks remain
manual verification; automated checks do not establish accessibility certification.

## Recommended next work

| Priority | Idea | Benefit and a concrete acceptance check | Starting point |
| --- | --- | --- | --- |
| 1 | Saved-day previews | Quick Add previews the start/end before saving, and its receipt links to the tracking day it counts towards (the plain receipt exists). Weight receipts keep calendar dates. | `sessions.edit`, `weight.daily-entry` |
| 2 | Remember useful duration presets | Allow two or three named per-device custom durations and remember an unfinished duration draft. Keep the seven additive buttons and exact fields available. Verify cancel, relaunch and credential changes do not create sessions. | `sessions.edit` |
| 2 | Weekly review | One compact view shows subject time, goal progress and weight trend in separate sections over an explicit date window. Avoid implying that correlations explain body-weight changes. Verify totals against the existing analytics and plot rules. | `graphs.tracking`, `goals.milestones`, `weight.plot` |
| 2 | Complete desktop weight-plot parity | Add native fullscreen, custom date windows and direct plot export where the phone already provides them. Reuse current plot math and verify identical selected rows and smoothers on both clients. | `weight.plot`, `native.shell` |
| 2 | Goal deadline filters | Add unobtrusive overdue/this-week filters and deadline badges using the existing goal deadline field. Keep milestone completion rules unchanged and verify pending/finished goals appear in the correct filters. | `goals.milestones` |
| 2 | Recurrence preview | Show the next due date and the next generated goal name while editing a template. Validate weekly/monthly limits before saving, including short months and the tracking day boundary. | `goals.recurrence` |
| 2 | Backup visibility and restore preview | Show the most recent local backup and offer a read-only preview of incoming rows/conflicts before restore. Verify a failed preview leaves the database and queued writes unchanged. Retain existing rotating backups. | `backups.restore` |
| 2 | Local diagnostic export | Export version, queue count, recent error categories and integrity status with credentials and personal history excluded. Make it an explicit local export; no telemetry service. Test the file against synthetic secrets. | `sync.replication`, `release.packaging` |
| 3 | Move several sessions together | Select rows by UID and preview ±1-day or clock shifts before one grouped action. Require a coherent retry/undo design and verify duration, fractional endpoints and cross-day selection behavior. | `sessions.edit` |
| 3 | Named plot presets | Save combinations of range, smoothing and visible series locally, building on existing graph options. Verify fullscreen/rotation preserves the preset and measurement draft. | `weight.plot`, `graphs.tracking` |
| 3 | Optional missed-day cue | A quiet in-app indication that today's weight is missing, with a direct route to the existing picker. Keep it optional, without notification prompts or added steps on the input screen. | `weight.daily-entry` |
| 3 | Continue measuring responsiveness | Record synthetic cold launch, navigation, long-list rendering and chart redraw timings in a repeatable local benchmark. Cache or defer work only where measurements identify a problem. Retain draft, offline and timestamp regressions. | `mobile.shell`, `graphs.tracking`, `weight.plot` |

## Keeping implementation manageable

Ship the priority-1 items individually, with a narrow measurable acceptance
check. Reuse feature factories, the shared sheet action guard and service
facades; add owners/tests to [features.json](../architecture/features.json) and
regenerate checked maps after moves. Avoid a general framework, a second state
store or a broad database migration just to support these conveniences.

Every write continues through the current atomic desktop outbox or durable
phone outbox and authenticated operation endpoint. Presets, previews
and diagnostics should be read-only or per-device until a feature explicitly
needs shared state. Use isolated fixtures and the existing release guards.
