# Native UI and chart details

Read this reference when the task touches these owners. Start with the shared
[engineering rules](../engineering-rules.md) and [task index](../index.md).

## Dialogs are inline, never native windows (macOS constraint)

**Never show a `QDialog` as its own window, and never reparent one into the
main window.** On macOS `QDialog.done()` can recreate the dialog's original
native `NSWindow` after it has been reparented; in native fullscreen macOS then
promotes that orphan into its own Space. Every editor in this app therefore
subclasses `InlineDialog` (a plain `QWidget` for its whole lifetime) from
`ui/widgets/dialog_utils.py`, which provides the `QDialog`-shaped API the code
expects (`accept/reject/done/result/finished/accepted/rejected`, plus
`Accepted`/`Rejected`).

- Show one with `open_dialog(dialog, on_finished)`; it hosts the widget in an
  `_InlineDialogLayer` over the main window and hands the result to the
  callback. Message boxes go through `information/warning/critical/question`
  from the same module — they build an inline `_InlineMessageDialog`, so they
  are **callback-based, not blocking**. Don't call `QMessageBox.exec()`.
- `dialog_owner(dialog)` returns the caller-supplied parent (the inline layer
  reparents), which is how nested dialogs reach `MainWindow` helpers such as
  `_register_undo`.
- The window property `_jt_inline_dialog_count` tracks open inline dialogs; the
  global shortcuts refuse to fire while it is non-zero.
- Same family of macOS bugs: `ReorderableCardList` hides shortcut badges during
  a drag, and `subjects_mixin._update_tracking_state_inplace()` mutates the
  existing cards on start/stop instead of rebuilding the list. Rebuilding the
  card list mid-interaction is what trips the fullscreen compositor. Keep both.

## Keyboard shortcuts and the one-step undo

Owned by `ui/controllers/keyboard.py`, through the stable
`MainWindow._install_shortcuts()` adapter: all `Qt.WindowShortcut`, all
gated on `_shortcut_focus_allows_navigation()` (no inline dialog, popup, modal,
or text-entry widget focused):

- `←` / `→` — move between the pages in stack order.
- `⌘1`–`⌘4` — open Goals, Subjects, Graphs or Weight directly.
- `1`–`9` — act on that row of the current page: open the goal (Goals page) or
  start/switch to that subject (Subjects page). Kept as **per-digit**
  `QShortcut`s so the switch-confirm prompt can disable the one conflicting key
  while it is open (two live shortcuts on one key are ambiguous to Qt and
  neither fires). Badges renumber with the list order.
- `Esc` — leave the Completed-goals or Archived-subjects view.
- In the expanded Weight plot, `Esc` restores the sidebar, entry and history,
  including an unfinished weight draft. An open inline editor or combo popup
  receives Escape first. Navigating to another page also leaves plot fullscreen.
- `W` / `M` / `Y` / `A` — graph range presets (Graphs page only); they write
  `graph_range` and clear any custom range.
- `Ctrl+Z` / `⌘Z` — the undo.

Undo is **one in-memory step, not a stack**: `_register_undo(callback)` stores a
single reversible action and `_perform_undo()` consumes it and reloads.
Currently registered by archive/unarchive of a subject, subject and goal
reordering, and goal-dialog actions. Keep it deliberately small — don't grow it
into a general undo stack, and don't register anything destructive that can't be
reversed by the stored callback alone.

## The heatmap was removed

There are two graph views, **Stacked Bar and Agenda** — on the desktop and on the
phone. The heatmap (widget, service method, API endpoint and its tests) is gone:
it answered the same question as the bar chart, less precisely. A saved
`graph_view_mode` of `heatmap` folds back to `bar` at launch so nobody lands on a
blank page. Don't reintroduce it without being asked.

`DaySessionsDialog` stays — it is what opens when a day is clicked in either
remaining view, via `get_sessions_for_logical_day()`. It edits / duplicates /
deletes the selected session itself (`apply_session_edits()`,
`duplicate_session()`), and "Open subject history…" hands the same session id to
`ManageSessionsDialog` so it is preselected there.

## Clicking a session must always target THAT session

Graph views carry session identity, never just a day:

- The agenda keeps `session_id` on every painted block. **Either mouse button
  behaves the same**: a click on a block edits that exact session
  (`AgendaViewWidget.session_clicked`), a click on empty column space opens the
  day (`day_clicked`). A block with no id (the live session) falls back to the
  day.
- Every session list stores the whole session dict per row and accepts
  `select_session_id` so the right row is preselected. Never reopen a list and
  rely on "row 0" — that was a real bug: editing from the agenda used to land
  on the subject's newest session.
- If an explicitly selected session leaves a day list after a move, clear the
  selection. Repeated move taps must never act on another row by default.
- Manual adds and edits require at least 30 seconds. Reject a short edit before
  mutation so the existing session survives, and validate phone inputs before
  enqueue so an invalid request cannot block later writes. The timer-stop rule
  for discarding a newly timed sub-30-second session remains separate.

## One session list, one set of session actions

`ui/widgets/session_list.py` is the single implementation shared by
`ManageSessionsDialog` (per subject) and `DaySessionsDialog` (per logical day):
`SessionListView` (rows, colour dot, selection styling, Enter/double-click),
`build_move_row()`, and the `edit_session` / `duplicate_session_to_today` /
`delete_session` / `shift_session` actions with their confirm copy.

- Rows are plain dicts (`session_id, subject_id, subject_name, color,
  start_time, end_time, duration_seconds`); `session_row()` adapts a `Session`
  model. `session_id` is None only for the live session, which is shown but
  never editable (`require_editable`).
- Reaching "the sessions menu" from a subject card or from a graph must land on
  this code, not a lookalike. If you add an action, add it here. `graphs_mixin`
  routes agenda clicks through `edit_session()` for exactly this reason — when
  it had its own copy, the agenda silently missed delete.
- `SessionDialog` reports `DELETE_RESULT` (like `GoalDialog`) instead of
  deleting itself; `edit_session()` performs the removal. Every entry point
  (agenda block, per-day list, per-subject list) therefore deletes identically.
- `session_dialog` imports `build_move_row` from this module, so `session_list`
  imports `SessionDialog` lazily inside `edit_session()` — keep it that way or
  the import graph becomes cyclic.
- Theme tokens come from `resolve_tokens()`, which walks up to the window;
  reading `parent._tokens` fails when a dialog opens another dialog.

Rules when extending:

- Weeks are Monday-start. Don't change that convention.
- The bar chart attributes a whole session to the logical day of its START. The
  agenda clamps sessions that spill past the logical-day end. Keep both.
- Animations pause when the app is unfocused/minimized (`FxBackgroundWidget.
  set_animating`, driven by `applicationStateChanged` / `changeEvent`). Don't
  reintroduce always-on full-window repaints.
- Settings/graph dialogs persist via the `db` settings table. Current graph
  keys: `graph_range` (`weeks|months|year|all|custom`), `graph_view_mode`
  (`bar|agenda`; legacy `heatmap` is normalized), `graph_custom_start` /
  `graph_custom_end` (only when range == "custom"), `graph_hour_start` / `graph_hour_end` (agenda window,
  defaults 6 / 23), `graph_fit_horizontal` (default "1"), `graph_autofit_hours`
  (default "0"). Other keys: `theme_fx`, `theme_palette`, `day_start_time`,
  `todo_order_mode` (`manual|deadline`), `device_id`. Only `day_start_time` is
  shared between machines — see `core/sync_policy.py`.
- **`graph_grouping` is a real preference**: `auto` (default) derives the bucket
  size from the range as before, and `daily`/`weekly`/`monthly` override it.
  `resolve_grouping()` applies it. It used to be deleted at launch as obsolete;
  it is not.
- **`days=N` with weekly/monthly grouping snaps to whole buckets** — "60 days"
  becomes "2 whole months" (`_resolve_logical_window`). That is deliberate: a
  rolling window cut into weeks would draw stubby half-height bars at both ends.
  It also means totals are only comparable across bucket sizes when the window is
  given as explicit `start_date`/`end_date`, which is how the desktop presets do
  it. Don't "fix" the apparent missing hours; there is a test pinning this.

## Colours in stylesheets

**Never write `#RRGGBBAA` (8-digit hex) in a Qt stylesheet.** Qt parses it as
`#AARRGGBB`, so `"{ACCENT}44"` on `#3B82F6` renders **green** (`rgb(130,246,68)`
at alpha 59), not a faded blue. This silently mis-coloured the session-list
selection and the Glow/Clean card borders.

Use `rgba(r, g, b, a)` with an integer alpha 0–255 — Qt honours it (`68` renders
identically to `0.27`). `themes.py` precomputes `<TOKEN>_A<alpha>` variants for
this; add the alpha you need to the tuple in `get_tokens()` rather than
concatenating hex digits.

## Layout and visual conventions

Preserve these established layout choices:

- A capped/centred content column. Content is deliberately **full-bleed**: the
  screen should look filled at fullscreen width.
- A dark contrast stroke around the bar-chart totals. The numerals keep only
  their coloured glow (`_intensity_style` tiers) over `TEXT_PRIMARY`.
- Any special colouring for today's agenda column. Every column is styled
  identically; the current-time marker line is the only "now" cue.

Current conventions:

- Card and chart surfaces are **near-opaque** (`*_A248`, panels at alpha 238) so
  the animated wash never shows through content. Don't lower these back.
- Subject/goal rows carry colour only in the dot and the left border; body text
  stays neutral. Chart fills keep full colour.
- Row heights are a deliberate middle ground: subject cards 68px, goal cards
  start at 56px and grow for notes, deadlines or milestones. Card-list rows take
  the viewport width so trailing actions remain reachable in half-screen windows.
- The shared inline layer supplies each dialog's visible title and close button;
  avoid repeating that heading inside its content. Subject history keeps Quick
  Add and move presets behind disclosures, leaving the selected session visible.
- Daily axis labels read `Mon 03` (weekday + day). No gridlines on the bar chart
  by choice — the two edge labels are the whole axis.
- Agenda blocks are a neutral base + ~35% subject tint + a 3px saturated left
  bar, with labels elided and clipped to their block.
- The goal ✓ is a quiet ring (`TEXT_DIMMED`) that turns `ACCENT_GREEN` on hover;
  a permanently saturated green circle fought with the card palette.
- A card's description must never wrap: `_ElidedLabel` truncates it and needs an
  **Expanding** size policy, otherwise Qt spreads the row's slack evenly and
  pushes the badge and ✓ into mid-card.
