# Time model

## Tracking logical day

Tracking uses naive local wall-clock datetimes. [timeutils.py](../../jobtracker/core/timeutils.py) owns parsing, duration, logical-day and agenda-hour calculations. A tracking day starts at the configured `day_start_time`, normally `03:00`. At `02:59:59` a session belongs to the previous date; at `03:00:00` it belongs to the current date. Fractional boundaries such as `03:30` are supported.

Windows are half-open: `[start, end)`. Weeks start Monday. Bar charts attribute the whole session to the logical day of its start; agenda blocks clamp the visible segment to the displayed logical day. Future entries, overlapping sessions and long sessions remain valid data. The agenda current-time marker follows the same logical-day boundary.

This refactor preserves wall-clock semantics. It adds no timezone conversion or daylight-saving reinterpretation. [Shared boundary cases](../../tests/contracts/time_boundaries.json) cover midnight, leap/year boundaries, custom boundaries and daylight-saving dates; [Python](../../tests/test_time_contracts.py) and the [phone tests](../../tests/mobile/agenda-now.test.mjs) consume the same cases.

Agenda auto-fit keeps work throughout the configured logical day visible,
including hours beyond 27 for later boundaries: a `06:30` day can end at hour `30.5`, and a
`23:45` day at `47.75`. The outward-rounded display axis can therefore reach 48.
[The Python resolver](../../jobtracker/services/graph_window.py),
[phone layout tests](../../tests/mobile/agenda-layout.test.mjs) and native Swift
layout checks consume the shared `agenda_windows` cases; Qt also has a canvas
rendering regression. The phone retains its
normal hour band as a minimum; Mac auto-fit hugs the recorded work. Manual hour
preferences retain their existing limits. These display differences do not
change session-start attribution, logical-day clamping or stored timestamps.

Session moves use `timeutils.shift_session_by_seconds`: ±1 day moves both local
endpoints by 86400 recorded seconds and preserves duration and identity. Phone
quick-add uses the [duration helper](../../web/features/sessions/duration.js)
to subtract the selected wall-clock minutes from one captured end time. Its UTC
surrogate is calendar arithmetic only; stored values remain naive local ISO.
Editors retain each original endpoint's fractional precision while its displayed
field is unchanged. Editing one endpoint does not round the other.

## Weight calendar day

Weight entry uses the calendar date, independent of the tracking-day boundary. A new daily entry is stored at midnight. Imported scale readings retain their original timestamps and precision. Multiple manual saves on the same date update one canonical reading; queued identities become aliases so later edits and deletes still resolve after coalescing.

[WeightService.upsert](../../jobtracker/services/weight_service.py) owns daily identity rules; [WeightRepository](../../jobtracker/core/persistence/weights.py) owns SQL; [weight_codec](../../jobtracker/core/persistence/weight_codec.py) owns input normalization. [Daily edge tests](../../tests/test_weight_daily_edges.py) verify offline reopen, aliases, date corrections and mirror repair.
