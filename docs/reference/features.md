# Feature behavior and compatibility details

Read this reference when the task touches these owners. Start with the shared
[engineering rules](../engineering-rules.md) and [task index](../index.md).

## Feature owners and stable facades

Use `python scripts/dev.py context weight.daily-entry` (or `timer.sessions`) to
find owners, contracts and focused checks. The [feature index](../generated/feature-index.md)
and [delegation interfaces](../generated/interfaces.md) are generated from explicit
source metadata; update the registry when ownership changes. Do not hand-edit
these maps. [Module boundaries](../architecture/boundaries.md) describes allowed
imports and compatibility seams.

`Database`, `TrackerService` and `SyncedTrackerService` keep their import paths,
method signatures, state ownership and override dispatch. `Database` owns one
connection and atomic scope; its repositories use that facade. `TrackerService`
owns the active session and composes feature services through the same facade.
Queue-aware write overrides remain explicit in `SyncedTrackerService`. Keep
explicit delegation, private monkeypatch points and injected service use.

Pure core logic belongs in these owners, rather than in widgets:

- `core/timeutils.py` — parsing, durations, logical day, week/month bucket keys,
  `agenda_hour()` (after-midnight work maps to 24..27), `split_by_logical_day()`,
  `clock_time_in_logical_day()` (inverse of `logical_day`) and
  `shift_session_to_logical_day()` (used by session duplication).
- `core/recovery.py` — crash-recovery decision: `build_recovery_info()` returns
  None for small gaps (don't prompt), else the numbers the dialog shows;
  `end_time_for_choice()` maps a choice to an end datetime. Gap threshold default
  5 min. The dialog (`ui/widgets/recovery_dialog.py`) is a thin shell.
- `core/colors.py` — `suggest_colors(existing_active_colors)` for the subject
  dialog. Only ACTIVE (non-archived) subject colours are passed in.
- `core/export_bundle.py` — CSV builders + `write_zip()`. JSON stays the only
  restore format; CSVs are read-only. Daily summary respects the logical day.
- `core/auto_backup.py` — rotating JSON safety copies written on every clean
  quit to `DATA_DIR/backups/` (`autobackup_*.json`, newest 10 kept). Only
  `autobackup_*` files are ever pruned; a backup failure must never block
  quitting.
- `core/sync_policy.py` — what is shared between machines and how rows are
  identified (`SYNCED_TABLES`, `SYNCED_SETTING_KEYS`, `new_uid()`, the uid
  triggers). See "Row identity across machines" below.

The UI shell is `ui/app.py` (`MainWindow`) with `SubjectsMixin`, `GoalsMixin`,
`GraphsMixin`, and the keyboard/sync controllers under `ui/controllers/`. The
window retains Qt ownership, lifecycle and compatibility method adapters. `MainWindow` owns the single
`TrackerService` as `self.service`; **widgets and mixins never touch `Database`
directly**, they go through the injected service (dialogs take it as a
constructor argument). Page order in the stack is **0 = Goals, 1 = Subjects,
2 = Graphs, 3 = Weight** — existing shortcut handlers keep their indices.
Weight widgets use `list_weight_measurements()`, `upsert_weight()` and
`delete_weight()` through that service. Synced mutations persist their outbox
entry atomically. New manual entries use calendar dates at midnight, independent
of the tracking-day boundary; imported measurements keep their timestamp and
precision. Weight display preferences stay device-local.

Daily weight entry has seven whole-kilogram tiles centered at the rounded
latest reading and ten decimal tiles `.0`–`.9`. Require both selections before
Save; keep the tiles selected until a durable save. On the phone, successful
online **or queued** saves clear only the captured unchanged draft; a newer
in-flight selection and failed/non-durable writes keep their value. Refreshes
on the phone must never preselect tiles or erase an edit. Backfilling an older day must not
move the latest-reading anchor backward. Original imported precision and exact
timestamps survive an edit until the value is deliberately changed. Automatic
Today advances at calendar midnight; explicit dates and unfinished drafts keep
their chosen day. Date, range shifts and UI options stay available without
cluttering the default full-width phone entry.

Phone composition lives in `web/app.js`; features own UI state under
`web/features/`, data transport/mirror/outbox under `web/data/`, and shared
presentation under `web/shared/`. Preserve compatibility re-exports. Features
receive explicit dependencies and never import the composition shell. Keep
request epochs, mutation revisions, draft ownership and the existing DOM for
fullscreen/rotation. Shared fullscreen must work with an iOS viewport fallback,
close on navigation, and restore focus/scroll without reparenting the plot.

`ui/widgets/dialog_utils.py` and `ui/widgets/reorderable_list.py` are shared UI
infrastructure; see the two sections below before adding dialogs or list cards.

Goal editing lives in `ui/widgets/goal_dialog.py` (`GoalDialog`). An unused
`todo_task_dialog.py` used to sit beside it and was deleted — don't recreate it.

Service methods doing the analytics (all logical-day aware, include the live
session): `get_subject_breakdown(grouping=daily|weekly|monthly, days/start/end)`,
`get_agenda_data()`, `get_subject_deletion_summary()`, `get_active_recovery_info()`,
`resolve_recovery()`, and `get_sessions_for_logical_day()`.
`get_daily_subject_breakdown()` is a thin compatibility wrapper. Subject card totals come from `get_subject_stats_map()`
(one GROUP BY for all subjects + the live session, attributed by its START
time like the bar chart).

`TrackerService.duplicate_session(session_id, to="today"|"next_day")` copies a
closed session onto another logical day keeping clock time, duration, and
subject. `shift_session(session_id, seconds)` nudges a session in time (both
ends move, duration preserved) — that is what the ±15m / ±1h buttons use.
Editing a session can also move it to another subject (subject dropdown in
`SessionDialog`); that is one UPDATE on the same row, so the session id and all
times survive.

**Sessions may be created in the future on purpose** (planning ahead). Nothing
blocks a future start, so graph windows must reach them: `graph_end_day()`
returns `max(today, logical day of the latest session)`. Don't reintroduce a
"no future sessions" guard.

**Anchor windows on the real today; only stretch the END to `graph_end_day()`.**
`_resolve_logical_window()` and `graphs_mixin._window_and_grouping()` both
compute their start from `logical_day(now)` and then extend the end. Anchoring
the whole window on `graph_end_day()` slides it into the future and silently
drops real past days — e.g. one session 7 days ahead pushed the "Weeks" preset
a whole week forward, hiding the week you were looking at. Regression test:
`test_future_session_extends_window_without_dropping_past_days`.

**Sessions have no note field.** `sessions.note` was dropped (the one
non-additive migration in the schema, see `_init_db`) because it was never used.
Subject notes (`tasks.notes`) and milestone notes stay. Don't add a session note
back without a real migration.

Switching subjects while tracking goes through `TrackerService.switch_subject()`
and, in the UI, always behind a confirm prompt: the running session keeps
ticking until the user confirms, and the old session gets the normal sub-30s
stop rule. Number-key shortcuts use the same confirm path — never a silent stop.

## Goals, milestones, and recurring generation

- A Goal is stored in the legacy `todo_tasks` table. It has a title
  (`name`), description (`notes`), completion state, order, an optional
  `template_id`, and the weekly-focus flag `is_focused`. Do not reconnect it to
  timed Subjects.
- **Weekly focus is a plain flag, not a mode.** `toggle_goal_focused()` flips
  `todo_tasks.is_focused`; the card shows ★/☆ (accent when focused) and the
  context menu offers "Focus This Week" / "Remove Weekly Focus" on active goals
  only. It **must never affect completion rules, ordering, or generation** — it
  is a visual marker that survives the JSON backup round-trip
  (`tests/test_focus.py`). No auto-clearing at week end.
- Completion is manual and milestone-gated in `TrackerService.complete_goal()`:
  all milestones must be checked, unless the goal has none. Adding or unchecking
  a milestone on a completed goal reopens it so the invariant stays true.
- Completed goals remain queryable and reopenable; their milestones are never
  hidden or deleted by completion.
- Active daily/weekly/monthly templates are checked on startup and when the
  Goals tab is opened. `last_generated` stores the logical period key. A due
  template inserts one ordinary editable goal at the top. Generation must stay
  idempotent within a period.
- **Generation is purely ADDITIVE.** Prior unfinished instances stay in the
  active list on purpose — the user wants a visible backlog of what still needs
  doing. Do not add auto-expiry, "missed" states, or any automatic removal; an
  earlier attempt at that was explicitly rejected.
- `goal_templates.recurrence_day` holds the schedule: ISO weekday 1–7 for
  weekly, day-of-month for monthly (clamped to the month's length), ignored for
  daily. `_template_is_due()` fires **on or after** that day (`>=`), so a
  template is never skipped just because the app wasn't opened that day.
- A template title may contain the `{date}` placeholder; it is replaced with the
  generated instance's logical date (`_template_instance_title`).
- The Goals page has an active list and a **Completed** view (toggle button /
  `Esc`). Goals are *completed*, never "archived" — archiving is a Subjects-only
  concept. Reopening happens from the goal dialog ("Reopen Goal") via
  `uncomplete_goal()`.
- The authoritative JSON backup includes goals, milestones, templates, their ID
  relationships, and settings. Restore must preserve repeated generated goals
  that legitimately share a title.
