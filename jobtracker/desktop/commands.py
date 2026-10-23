"""The explicit allowlist of native commands and their service calls.

Every command names its kind:

``read``      answers from the store; no receipt, no commit
``mutation``  database write; runs inside one ``Database.atomic`` scope together
              with its receipt and any sync-outbox entries the facade queues
``action``    lifecycle, file or credential work; never receipt-backed, because
              a SQLite transaction cannot make a file change atomic

Handlers resolve UIDs to local IDs here and call the injected facade normally,
so ``SyncedTrackerService`` overrides still queue their operations. There is no
attribute lookup by name, SQL, evaluation or arbitrary file access.
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from ..core import export_bundle, recovery, sync_policy, timeutils, weight_plot
from ..core.colors import suggest_colors
from ..core.themes import THEME_PRESETS, FX_NAMES, PALETTE_NAMES
from ..services import graph_window
from ..sync import settings as sync_settings
from . import dto

READ = "read"
MUTATION = "mutation"
ACTION = "action"

# Local preferences the native client may read and write. Everything else in
# the settings table (device identity, sync bookkeeping) stays engine-owned.
CLIENT_SETTING_KEYS = frozenset({
    "theme_fx", "theme_palette", "ui_reduce_motion",
    "graph_range", "graph_custom_start", "graph_custom_end", "graph_grouping",
    "graph_view_mode", "graph_hour_start", "graph_hour_end", "graph_autofit_hours",
    "graph_fit_horizontal",
    "weight_chart_historic", "weight_chart_points", "weight_chart_range",
    "weight_chart_smoothing", "weight_chart_span", "weight_picker_decimal",
    "weight_picker_whole", "weight_ui_contrast", "weight_ui_density",
    "subject_stats_filter",
})
STATS_FILTERS = ("Total", "Today", "Last 7 days", "Last 30 days")
MAX_TEXT = 20_000
MAX_PAGE = 500


class Missing(Exception):
    """The addressed row no longer exists."""


class Refused(Exception):
    """The existing service declined the request."""


class Invalid(Exception):
    """Malformed parameters."""


@dataclass(frozen=True)
class Command:
    name: str
    kind: str
    handler: Callable
    topics: tuple[str, ...] = ()


REGISTRY: dict[str, Command] = {}


def command(name: str, kind: str, topics: tuple[str, ...] = ()):
    def register(handler):
        REGISTRY[name] = Command(name, kind, handler, topics)
        return handler
    return register


# ── parameter helpers ──────────────────────────────────────────────────
def _text(params: dict, key: str, *, required: bool = True, default: str = "", limit: int = MAX_TEXT) -> str:
    value = params.get(key, None if required else default)
    if value is None:
        if required:
            raise Invalid(f"{key} is required")
        return default
    if not isinstance(value, str):
        raise Invalid(f"{key} must be text")
    if len(value) > limit:
        raise Invalid(f"{key} is too long")
    return value


def _optional_text(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    return _text(params, key)


def _bool(params: dict, key: str, default: bool | None = None) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise Invalid(f"{key} must be true or false")
    return value


def _int(params: dict, key: str, *, low: int, high: int, default: int | None = None) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Invalid(f"{key} must be an integer from {low} to {high}")
    return value


def _moment(params: dict, key: str, *, required: bool = True) -> datetime | None:
    raw = params.get(key)
    if raw is None and not required:
        return None
    parsed = timeutils.parse_iso(raw) if isinstance(raw, str) else None
    if parsed is None or not timeutils.is_local_datetime(parsed):
        raise Invalid(f"{key} must be a local ISO-8601 timestamp")
    return parsed


def _day(params: dict, key: str, *, required: bool = True) -> date | None:
    raw = params.get(key)
    if raw is None and not required:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise Invalid(f"{key} must be YYYY-MM-DD") from None


def _id(ctx, params: dict, key: str, table: str) -> int:
    uid = params.get(key)
    if not isinstance(uid, str) or not uid:
        raise Invalid(f"{key} is required")
    local = ctx.service.db.id_for_uid(table, uid)
    if local is None:
        raise Missing(f"{key} {uid} no longer exists")
    return local


def _ids(ctx, params: dict, key: str, table: str) -> list[int]:
    uids = params.get(key)
    if not isinstance(uids, list) or not all(isinstance(u, str) for u in uids):
        raise Invalid(f"{key} must be a list of identities")
    if len(set(uids)) != len(uids):
        raise Invalid(f"{key} contains duplicates")
    out = []
    for uid in uids:
        local = ctx.service.db.id_for_uid(table, uid)
        if local is None:
            raise Missing(f"{uid} no longer exists")
        out.append(local)
    return out


def _session_dto(ctx, row) -> dict:
    return dto.session(ctx.service.db, row, device_id=ctx.service.device_id)


def _closed_session(ctx, params: dict, key: str = "session_uid"):
    session_id = _id(ctx, params, key, "sessions")
    row = ctx.service.get_session(session_id)
    if row is None:
        raise Missing("session no longer exists")
    if row.end_time is None:
        raise Refused("A running session cannot be edited here")
    return row


# ── engine and settings ─────────────────────────────────────────────────
@command("engine.info", READ)
def engine_info(ctx, params):
    return ctx.info()


@command("settings.get", READ)
def settings_get(ctx, params):
    service = ctx.service
    values = {key: service.get_setting(key, "") for key in sorted(CLIENT_SETTING_KEYS)}
    return {
        "values": values,
        "day_start_time": timeutils.day_start_to_str(service.get_day_start()),
        "themes": [
            {"name": name, "fx": fx, "palette": palette}
            for name, (fx, palette) in THEME_PRESETS.items()
        ],
        "fx_names": FX_NAMES,
        "palette_names": PALETTE_NAMES,
    }


@command("settings.set", MUTATION, ("settings",))
def settings_set(ctx, params):
    values = params.get("values")
    if not isinstance(values, dict) or not values:
        raise Invalid("values must be a non-empty object")
    for key, value in values.items():
        if key == "day_start_time":
            try:
                ctx.service.set_day_start(_text(values, key, limit=5))
            except ValueError as exc:
                raise Invalid(str(exc)) from None
            continue
        if key not in CLIENT_SETTING_KEYS:
            raise Invalid(f"{key} is not a client setting")
        ctx.service.set_setting(key, _text(values, key, limit=200))
    topics = {"settings"}
    if {"theme_fx", "theme_palette", "ui_reduce_motion"} & set(values):
        topics.add("appearance")
    if "day_start_time" in values:
        topics |= {"subjects", "sessions", "graphs", "goals"}
    ctx.extra_topics(topics)
    return settings_get(ctx, {})


# ── subjects ────────────────────────────────────────────────────────────
@command("subjects.list", READ)
def subjects_list(ctx, params):
    archived = _bool(params, "archived", False)
    stats_filter = params.get("filter", "Total")
    if stats_filter not in STATS_FILTERS:
        raise Invalid("filter must be one of " + ", ".join(STATS_FILTERS))
    service = ctx.service
    totals = service.get_subject_stats_map(stats_filter)
    today = totals if stats_filter == "Today" else service.get_subject_stats_map("Today")
    rows = service.get_all_subjects(archived=archived)
    resume = None if archived else service.get_last_tracked_subject()
    active_colors = [row.color for row in (service.get_all_subjects(archived=False) if archived else rows)]
    return {
        "archived": archived,
        "filter": stats_filter,
        "subjects": [dto.subject(service.db, row, totals.get(row.id, 0)) for row in rows],
        "resume_subject_uid": resume.uid if resume is not None else None,
        "today_seconds": sum(today.values()),
        "suggested_colors": suggest_colors(active_colors, count=3),
        "timer": ctx.timer_snapshot(),
    }


@command("subject.deletion_summary", READ)
def subject_deletion_summary(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    summary = ctx.service.get_subject_deletion_summary(subject_id)
    return {key: summary[key] for key in ("session_count", "total_seconds", "earliest", "latest")}


@command("subject.add", MUTATION, ("subjects",))
def subject_add(ctx, params):
    created = ctx.service.add_subject(
        _text(params, "name", limit=200), _text(params, "color", limit=32),
        _text(params, "notes", required=False),
    )
    if created is None:
        raise Refused("A subject needs a unique, non-empty name")
    return {"subject": dto.subject(ctx.service.db, created, 0)}


@command("subject.update", MUTATION, ("subjects", "timer", "sessions", "graphs"))
def subject_update(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    updated = ctx.service.update_subject(
        subject_id, _text(params, "name", limit=200), _text(params, "color", limit=32),
        _text(params, "notes", required=False),
    )
    if updated is None:
        raise Refused("A subject needs a unique, non-empty name")
    return {"subject": dto.subject(ctx.service.db, updated)}


@command("subject.archive", MUTATION, ("subjects", "timer", "graphs"))
def subject_archive(ctx, params):
    ctx.service.archive_subject(_id(ctx, params, "subject_uid", "tasks"))
    return {}


@command("subject.unarchive", MUTATION, ("subjects", "graphs"))
def subject_unarchive(ctx, params):
    ctx.service.unarchive_subject(_id(ctx, params, "subject_uid", "tasks"))
    return {}


@command("subject.delete", MUTATION, ("subjects", "timer", "sessions", "graphs"))
def subject_delete(ctx, params):
    ctx.service.delete_subject(_id(ctx, params, "subject_uid", "tasks"))
    return {}


@command("subject.reorder", MUTATION, ("subjects",))
def subject_reorder(ctx, params):
    archived = _bool(params, "archived", False)
    ctx.service.set_subject_order(_ids(ctx, params, "subject_uids", "tasks"), archived=archived)
    return {}


# ── timer and recovery ─────────────────────────────────────────────────
@command("timer.get", READ)
def timer_get(ctx, params):
    return ctx.timer_snapshot()


@command("timer.start", MUTATION, ("timer", "subjects", "graphs"))
def timer_start(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    if not ctx.service.start_subject(subject_id, start_time=_moment(params, "at")):
        raise Refused("The timer could not start; another session may be running")
    return ctx.timer_snapshot()


@command("timer.stop", MUTATION, ("timer", "subjects", "sessions", "graphs"))
def timer_stop(ctx, params):
    expected = _text(params, "session_uid")
    at = _moment(params, "at")
    active = ctx.service._persisted_active_session()
    if active is None or active.uid != expected:
        raise Refused("The timer has already changed")
    ctx.service.stop_active_subject(at)
    if ctx.service.active_session is not None:
        raise Refused("The session could not be stopped at that time")
    kept = ctx.service.db.id_for_uid("sessions", expected) is not None
    return {"kept": kept, "session_uid": expected, "timer": ctx.timer_snapshot()}


@command("timer.switch", MUTATION, ("timer", "subjects", "sessions", "graphs"))
def timer_switch(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    expected = params.get("source_session_uid")
    active = ctx.service._persisted_active_session()
    if (active.uid if active is not None else None) != expected:
        raise Refused("The timer has already changed")
    if not ctx.service.switch_subject(subject_id, moment=_moment(params, "at")):
        raise Refused("The timer could not switch to that subject")
    return ctx.timer_snapshot()


@command("recovery.resolve", MUTATION, ("timer", "subjects", "sessions", "graphs"))
def recovery_resolve(ctx, params):
    """Close the recovered session using the prompt's choice.

    The end time comes from ``core.recovery.end_time_for_choice`` against the
    moment the prompt was shown, so custom values are clamped exactly as in the
    Qt dialog and the client never computes an end time itself.
    """
    session_id = _id(ctx, params, "session_uid", "sessions")
    active = ctx.service._persisted_active_session()
    if active is None or active.id != session_id:
        raise Refused("This session is no longer the recovered timer")
    choice = params.get("choice")
    if choice not in (recovery.CHOICE_LAST, recovery.CHOICE_NOW, recovery.CHOICE_CUSTOM_END, recovery.CHOICE_CUSTOM_LENGTH):
        raise Invalid("choice must be last, now, custom_end or custom_length")
    info = ctx.service.get_active_recovery_info(now=_moment(params, "prompt_now"), gap_threshold_seconds=0)
    if info is None:
        raise Refused("This session no longer needs recovery")
    custom_end = _moment(params, "custom_end", required=False) if choice == recovery.CHOICE_CUSTOM_END else None
    length = _int(params, "custom_length_seconds", low=0, high=400 * 86400) if choice == recovery.CHOICE_CUSTOM_LENGTH else None
    end = recovery.end_time_for_choice(info, choice, custom_end=custom_end, custom_length_seconds=length)
    ctx.service.resolve_recovery(end, session_id=session_id)
    if ctx.service.active_session is not None and ctx.service.active_session.id == session_id:
        raise Refused("The chosen end time is outside the session")
    ctx.recovery_settled()
    return ctx.timer_snapshot()


@command("recovery.dismiss", ACTION, ("timer",))
def recovery_dismiss(ctx, params):
    ctx.recovery_settled()
    return ctx.timer_snapshot()


# ── sessions ───────────────────────────────────────────────────────────
@command("session.get", READ)
def session_get(ctx, params):
    session_id = _id(ctx, params, "session_uid", "sessions")
    row = ctx.service.get_session(session_id)
    if row is None:
        raise Missing("session no longer exists")
    return {"session": _session_dto(ctx, row)}


@command("sessions.for_subject", READ)
def sessions_for_subject(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    offset = _int(params, "offset", low=0, high=10_000_000, default=0)
    limit = _int(params, "limit", low=1, high=MAX_PAGE, default=200)
    rows = ctx.service.get_sessions_for_subject(subject_id)
    page = rows[offset: offset + limit]
    return {
        "sessions": [_session_dto(ctx, row) for row in page],
        "total": len(rows),
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
    }


@command("sessions.for_day", READ)
def sessions_for_day(ctx, params):
    day = _day(params, "day")
    codec = sync_policy.WireCodec(ctx.service.db)
    rows = ctx.service.get_sessions_for_logical_day(day)
    return {"day": day.isoformat(), "sessions": [dto.day_session(codec, row) for row in rows]}


@command("session.add", MUTATION, ("sessions", "subjects", "graphs"))
def session_add(ctx, params):
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    created = ctx.service.add_session(subject_id, _moment(params, "start_time"), _moment(params, "end_time"))
    if created is None:
        raise Refused("Sessions must end after they start and last at least 30 seconds")
    return {"session": _session_dto(ctx, created)}


@command("session.quick_add", MUTATION, ("sessions", "subjects", "graphs"))
def session_quick_add(ctx, params):
    """A session of exactly ``minutes`` ending at the captured click time.

    Like the Qt quick add, the start is not clamped to midnight: the logical
    day attributes it to the day it began.
    """
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    minutes = _int(params, "minutes", low=1, high=99 * 60 + 59)
    end = _moment(params, "at")
    created = ctx.service.add_session(subject_id, end - timedelta(minutes=minutes), end)
    if created is None:
        raise Refused("The session was not saved. Check that its subject still exists and its duration is at least 30 seconds.")
    return {"session": _session_dto(ctx, created)}


@command("session.add_slot", MUTATION, ("sessions", "subjects", "graphs"))
def session_add_slot(ctx, params):
    """A fixed whole-hour slot on the captured click's calendar date."""
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    start_hour = _int(params, "start_hour", low=0, high=23)
    end_hour = _int(params, "end_hour", low=1, high=24)
    if end_hour <= start_hour:
        raise Invalid("end_hour must be after start_hour")
    day = _moment(params, "at").date()
    start = datetime.combine(day, time(hour=start_hour))
    end = datetime.combine(day, time()) + timedelta(hours=end_hour)
    created = ctx.service.add_session(subject_id, start, end)
    if created is None:
        raise Refused("The session was not saved. Check that its subject still exists.")
    return {"session": _session_dto(ctx, created)}


@command("session.update", MUTATION, ("sessions", "subjects", "graphs"))
def session_update(ctx, params):
    row = _closed_session(ctx, params)
    subject_id = _id(ctx, params, "subject_uid", "tasks")
    start = params.get("start_time")
    end = params.get("end_time")
    # Unchanged endpoints keep their exact stored strings (and precision).
    start_dt = timeutils.parse_iso(row.start_time) if start == row.start_time else _moment(params, "start_time")
    end_dt = timeutils.parse_iso(row.end_time) if end == row.end_time else _moment(params, "end_time")
    updated = ctx.service.update_session(row.id, subject_id, start_dt, end_dt)
    if updated is None:
        raise Refused("Sessions must end after they start and last at least 30 seconds")
    return {"session": _session_dto(ctx, ctx.service.get_session(row.id))}


@command("session.delete", MUTATION, ("sessions", "subjects", "graphs", "timer"))
def session_delete(ctx, params):
    session_id = _id(ctx, params, "session_uid", "sessions")
    ctx.service.delete_session(session_id)
    return {}


@command("session.shift", MUTATION, ("sessions", "subjects", "graphs"))
def session_shift(ctx, params):
    row = _closed_session(ctx, params)
    seconds = _int(params, "seconds", low=-400 * 86400, high=400 * 86400)
    moved = ctx.service.shift_session(row.id, seconds)
    if moved is None:
        raise Refused("The session could not be moved")
    return {"session": _session_dto(ctx, ctx.service.get_session(row.id))}


@command("session.duplicate", MUTATION, ("sessions", "subjects", "graphs"))
def session_duplicate(ctx, params):
    row = _closed_session(ctx, params)
    target = params.get("to")
    if target not in ("today", "next_day"):
        raise Invalid("to must be today or next_day")
    created = ctx.service.duplicate_session(row.id, to=target)
    if created is None:
        raise Refused("The session could not be duplicated")
    return {"session": _session_dto(ctx, created)}


# ── goals, milestones and templates ────────────────────────────────────
@command("goals.list", READ)
def goals_list(ctx, params):
    completed = _bool(params, "completed", False)
    service = ctx.service
    rows = service.get_completed_goals() if completed else service.get_active_goals()
    return {"completed": completed, "goals": [dto.goal(service, row) for row in rows]}


@command("goal.add", MUTATION, ("goals",))
def goal_add(ctx, params):
    created = ctx.service.add_todo_task(
        _text(params, "name", limit=500), _text(params, "notes", required=False),
        _optional_text(params, "deadline"),
    )
    if created is None:
        raise Refused("A goal needs a title")
    return {"goal": dto.goal(ctx.service, created)}


@command("goal.update", MUTATION, ("goals",))
def goal_update(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    updated = ctx.service.update_todo_task(
        goal_id, _text(params, "name", limit=500), _text(params, "notes", required=False),
        _optional_text(params, "deadline"),
    )
    if updated is None:
        raise Refused("A goal needs a title")
    return {"goal": dto.goal(ctx.service, updated)}


@command("goal.save", MUTATION, ("goals",))
def goal_save(ctx, params):
    """Create or edit a goal with its complete milestone list in one commit.

    Mirrors the Qt editor's ``apply_goal_edits``: unlisted milestones are
    deleted, listed ones updated, new ones appended, all through the facade so
    each change queues its usual operation, inside one receipt-backed scope.
    """
    name = _text(params, "name", limit=500)
    notes = _text(params, "notes", required=False)
    deadline = _optional_text(params, "deadline")
    entries = params.get("milestones", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise Invalid("milestones must be a list of objects")
    service = ctx.service
    if params.get("goal_uid") is None:
        goal = service.add_todo_task(name, notes, deadline)
        if goal is None:
            raise Refused("A goal needs a title")
        goal_id = goal.id
    else:
        goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
        if service.update_todo_task(goal_id, name, notes, deadline) is None:
            raise Refused("A goal needs a title")
    existing = {m.uid: m for m in service.get_goal_milestones(goal_id)}
    submitted = {entry.get("uid") for entry in entries if entry.get("uid")}
    unknown = submitted - set(existing)
    if unknown:
        raise Missing("A milestone was removed elsewhere; reopen the goal to edit it")
    for uid in set(existing) - submitted:
        service.delete_milestone(existing[uid].id)
    for entry in entries:
        title = _text(entry, "title", limit=500).strip()
        if not title:
            raise Invalid("Every milestone needs a title")
        note = _text(entry, "note", required=False)
        if entry.get("uid"):
            service.update_milestone(existing[entry["uid"]].id, title, note)
        elif service.add_milestone(goal_id, title, note) is None:
            raise Refused("A milestone could not be added")
    return {"goal": dto.goal(service, service.get_goal(goal_id))}


@command("goal.delete", MUTATION, ("goals",))
def goal_delete(ctx, params):
    ctx.service.delete_todo_task(_id(ctx, params, "goal_uid", "todo_tasks"))
    return {}


@command("goal.complete", MUTATION, ("goals",))
def goal_complete(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    if not ctx.service.complete_goal(goal_id):
        raise Refused("Finish every milestone before completing this goal")
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("goal.uncomplete", MUTATION, ("goals",))
def goal_uncomplete(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    ctx.service.uncomplete_goal(goal_id)
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("goal.toggle_focus", MUTATION, ("goals",))
def goal_toggle_focus(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    focused = ctx.service.toggle_goal_focused(goal_id)
    return {"focused": bool(focused)}


@command("goal.reorder", MUTATION, ("goals",))
def goal_reorder(ctx, params):
    completed = _bool(params, "completed", False)
    ctx.service.set_todo_task_order(_ids(ctx, params, "goal_uids", "todo_tasks"), completed=completed)
    return {}


@command("milestone.add", MUTATION, ("goals",))
def milestone_add(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    created = ctx.service.add_milestone(goal_id, _text(params, "title", limit=500), _text(params, "note", required=False))
    if created is None:
        raise Refused("A milestone needs a title")
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("milestone.update", MUTATION, ("goals",))
def milestone_update(ctx, params):
    milestone_id = _id(ctx, params, "milestone_uid", "milestones")
    updated = ctx.service.update_milestone(milestone_id, _text(params, "title", limit=500), _text(params, "note", required=False))
    if updated is None:
        raise Refused("A milestone needs a title")
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(updated.goal_id))}


@command("milestone.set_done", MUTATION, ("goals",))
def milestone_set_done(ctx, params):
    milestone_id = _id(ctx, params, "milestone_uid", "milestones")
    goal_id = ctx.service.db.get_milestone(milestone_id).goal_id
    ctx.service.set_milestone_done(milestone_id, _bool(params, "done"))
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("milestone.delete", MUTATION, ("goals",))
def milestone_delete(ctx, params):
    milestone_id = _id(ctx, params, "milestone_uid", "milestones")
    goal_id = ctx.service.db.get_milestone(milestone_id).goal_id
    ctx.service.delete_milestone(milestone_id)
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("milestone.reorder", MUTATION, ("goals",))
def milestone_reorder(ctx, params):
    goal_id = _id(ctx, params, "goal_uid", "todo_tasks")
    ctx.service.set_milestone_order(goal_id, _ids(ctx, params, "milestone_uids", "milestones"))
    return {"goal": dto.goal(ctx.service, ctx.service.get_goal(goal_id))}


@command("templates.list", READ)
def templates_list(ctx, params):
    return {"templates": [dto.template(row) for row in ctx.service.get_goal_templates()]}


def _template_args(params):
    recurrence = params.get("recurrence")
    if recurrence not in ("daily", "weekly", "monthly"):
        raise Invalid("recurrence must be daily, weekly or monthly")
    day = params.get("recurrence_day")
    if day is not None and (isinstance(day, bool) or not isinstance(day, int)):
        raise Invalid("recurrence_day must be an integer")
    milestones = params.get("milestones", [])
    if not isinstance(milestones, list):
        raise Invalid("milestones must be a list")
    specs = []
    for item in milestones:
        if not isinstance(item, dict):
            raise Invalid("each milestone must be an object")
        specs.append({"title": _text(item, "title", limit=500), "note": _text(item, "note", required=False)})
    return (_text(params, "title", limit=500), _text(params, "notes", required=False), recurrence, specs, day)


@command("template.add", MUTATION, ("templates", "goals"))
def template_add(ctx, params):
    title, notes, recurrence, specs, day = _template_args(params)
    created = ctx.service.add_goal_template(title, notes, recurrence, specs, day)
    if created is None:
        raise Refused("A template needs a title and valid schedule")
    return {"template": dto.template(created)}


@command("template.update", MUTATION, ("templates", "goals"))
def template_update(ctx, params):
    template_id = _id(ctx, params, "template_uid", "goal_templates")
    title, notes, recurrence, specs, day = _template_args(params)
    updated = ctx.service.update_goal_template(template_id, title, notes, recurrence, specs, day)
    if updated is None:
        raise Refused("A template needs a title and valid schedule")
    return {"template": dto.template(updated)}


@command("template.set_active", MUTATION, ("templates", "goals"))
def template_set_active(ctx, params):
    ctx.service.set_goal_template_active(_id(ctx, params, "template_uid", "goal_templates"), _bool(params, "active"))
    return {}


@command("template.delete", MUTATION, ("templates", "goals"))
def template_delete(ctx, params):
    ctx.service.delete_goal_template(_id(ctx, params, "template_uid", "goal_templates"))
    return {}


@command("goals.generate_due", MUTATION, ("goals", "templates"))
def goals_generate_due(ctx, params):
    created = ctx.service.generate_due_goal_instances()
    return {"goal_uids": [ctx.service.db.uid_for_id("todo_tasks", goal_id) for goal_id in created]}


# ── graphs ─────────────────────────────────────────────────────────────
def _graph_preferences(ctx, params) -> graph_window.GraphPreferences:
    prefs = graph_window.GraphPreferences.from_settings(ctx.service.get_setting)
    overrides = {}
    for key in ("preset", "grouping", "hour_start", "hour_end", "autofit_hours"):
        if key in params:
            overrides[key] = params[key]
    if "custom_start" in params or "custom_end" in params:
        start = _day(params, "custom_start", required=False)
        end = _day(params, "custom_end", required=False)
        overrides["custom_range"] = (start, end) if start and end else None
    try:
        return prefs.replace(**overrides)
    except ValueError as exc:
        raise Invalid(str(exc)) from None


@command("graphs.breakdown", READ)
def graphs_breakdown(ctx, params):
    prefs = _graph_preferences(ctx, params)
    start_day, end_day, grouping = graph_window.window_and_grouping(ctx.service, prefs)
    buckets = ctx.service.get_subject_breakdown(grouping=grouping, days=None, start_date=start_day, end_date=end_day)
    codec = sync_policy.WireCodec(ctx.service.db)
    return {
        "grouping": grouping,
        "range_label": graph_window.range_label(prefs),
        "start_day": start_day.isoformat() if start_day else None,
        "end_day": end_day.isoformat() if end_day else None,
        "fit_width": ctx.service.get_setting("graph_fit_horizontal", "1") == "1",
        "buckets": [dto.breakdown_bucket(codec, bucket) for bucket in buckets],
    }


@command("graphs.agenda", READ)
def graphs_agenda(ctx, params):
    prefs = _graph_preferences(ctx, params)
    agenda = graph_window.agenda_window(ctx.service, prefs)
    if (agenda.end_day - agenda.start_day).days > 400:
        raise Invalid("Agenda supports up to 400 days; choose a shorter range")
    codec = sync_policy.WireCodec(ctx.service.db)
    day_start = ctx.service.get_day_start()
    return {
        "days": [str(day) for day in agenda.day_keys],
        "sessions": [dto.agenda_block(codec, block) for block in agenda.sessions],
        "hour_start": agenda.hour_start,
        "hour_end": agenda.hour_end,
        "now_day": agenda.now_marker[0],
        "now_hour": agenda.now_marker[1],
        "day_start_hour": day_start.hour + day_start.minute / 60.0,
        "range_label": graph_window.range_label(prefs),
        "fit_width": ctx.service.get_setting("graph_fit_horizontal", "1") == "1",
    }


# ── weight ─────────────────────────────────────────────────────────────
@command("weight.list", READ)
def weight_list(ctx, params):
    return {"measurements": ctx.service.list_weight_measurements()}


@command("weight.view", READ)
def weight_view(ctx, params):
    """History, plot points, fitted trend, axes, summary and 30-day badge.

    Uses ``core.weight_plot`` exactly as the Qt chart does; the client draws
    the returned calendar-ordinal coordinates and never re-fits a trend.
    """
    get = ctx.service.get_setting
    choice = params.get("range", get("weight_chart_range", "90"))
    mode = params.get("smoothing", get("weight_chart_smoothing", "loess"))
    historic = params.get("historic", get("weight_chart_historic", "0") == "1")
    try:
        span = float(params.get("span", get("weight_chart_span", "0.04")))
    except (TypeError, ValueError):
        span = 0.04
    if choice not in ("30", "90", "365", "all") or mode not in ("loess", "mean7", "none") or not isinstance(historic, bool):
        raise Invalid("range, smoothing or historic is not supported")
    rows = [point.row for point in weight_plot.points_for(ctx.service.list_weight_measurements(), historic=True)]
    end = date.today()
    start = weight_plot.range_start(end, int(choice)) if choice != "all" else None
    points = weight_plot.points_for(rows, start=start, end=end if start else None, historic=historic)
    trend = weight_plot.trend_for(points, mode, span)
    result = {
        "rows": weight_plot.history_changes(rows),
        "points": [{"x": p.x, "y": p.y, "uid": p.row.get("uid"), "measured_at": p.row["measured_at"], "source": p.row.get("source")} for p in points],
        "trend": [[p.x, p.y] for p in trend],
        "range": choice, "smoothing": mode, "span": span, "historic": historic,
        "summary": None, "badge": None, "axis": None,
    }
    if points:
        first, last = points[0].x, points[-1].x
        if first == last:
            first, last = first - 0.5, last + 0.5
        result["axis"] = {
            "first": first, "last": last,
            "start_label": weight_plot.date_label(points[0].x, "%d %b"),
            "end_label": weight_plot.date_label(points[-1].x, "%d %b"),
            "years": [[ordinal, year] for ordinal, year in weight_plot.year_ticks(first, last)],
            "fallback_year": weight_plot.date_label(points[0].x, "%Y"),
        }
        result["summary"] = {"count": len(points), "latest": points[-1].y, "change": points[-1].y - points[0].y}
        latest_day = timeutils.parse_iso(points[-1].row["measured_at"]).date()
        result["badge"] = weight_plot.thirty_day_change(rows, latest_day, historic=historic)
    return result


@command("weight.upsert", MUTATION, ("weight",))
def weight_upsert(ctx, params):
    weight_uid = params.get("weight_uid")
    if weight_uid is not None and not isinstance(weight_uid, str):
        raise Invalid("weight_uid must be text")
    value = params.get("weight_kg")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("weight_kg must be a number")
    try:
        result = ctx.service.upsert_weight(_text(params, "measured_at", limit=64), value, weight_uid=weight_uid)
    except ValueError as exc:
        raise Invalid(str(exc)) from None
    if result.get("skipped"):
        raise Missing(result.get("reason") or "measurement no longer exists")
    return result


@command("weight.delete", MUTATION, ("weight",))
def weight_delete(ctx, params):
    if not ctx.service.delete_weight(_text(params, "weight_uid", limit=64)):
        raise Missing("measurement no longer exists")
    return {}


# ── backup ─────────────────────────────────────────────────────────────
def _user_file(params, key: str, suffixes: tuple[str, ...]) -> Path:
    raw = _text(params, key, limit=4096)
    path = Path(raw).expanduser()
    if not path.is_absolute() or path.suffix.lower() not in suffixes:
        raise Invalid(f"{key} must be an absolute {'/'.join(suffixes)} path chosen by the user")
    return path


@command("backup.import", MUTATION, ("subjects", "sessions", "goals", "templates", "weight", "settings", "appearance", "graphs", "timer"))
def backup_import(ctx, params):
    path = _user_file(params, "path", (".json", ".zip"))
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as bundle:
                data = json.loads(bundle.read(export_bundle.JSON_FILENAME))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise Invalid(f"The backup could not be read: {exc}") from None
    if not isinstance(data, dict):
        raise Invalid("The backup is not a JobTracker export")
    try:
        ctx.service.import_data(data)
    except ValueError as exc:
        raise Invalid(f"Import failed: {exc}") from None
    return {"imported": True}


@command("backup.export", ACTION)
def backup_export(ctx, params):
    path = _user_file(params, "path", (".zip", ".json"))
    export = ctx.service.export_data()
    descriptor, temporary = tempfile.mkstemp(prefix=".jobtracker-export-", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    try:
        if path.suffix.lower() == ".zip":
            breakdown = ctx.service.get_subject_breakdown(grouping="daily", days=None)
            export_bundle.write_zip(temporary, export, breakdown)
        else:
            Path(temporary).write_text(json.dumps(export, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise Refused(f"Export failed: {exc}") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(path)}


# ── sync configuration and status ──────────────────────────────────────
@command("sync.status", READ)
def sync_status(ctx, params):
    return ctx.sync_status()


@command("sync.configure", ACTION, ("sync",))
def sync_configure(ctx, params):
    service = ctx.service
    enabled = _bool(params, "enabled")
    url = _text(params, "server_url", required=False, limit=2048)
    token = params.get("token")
    if token is not None and not isinstance(token, str):
        raise Invalid("token must be text")
    # Matches Settings: an empty token keeps the saved one; it is never echoed.
    previous_token = sync_settings.read_token()
    token_changed = False
    try:
        with service.db.atomic():
            sync_settings.set_enabled(service, enabled)
            sync_settings.set_server_url(service, url)
            if token is not None and token.strip():
                sync_settings.write_token(token)
                token_changed = True
    except Exception:
        # File replacement is not part of SQLite's transaction. A failed
        # token write rolls back settings; a failed DB commit restores the
        # previous private token rather than leaving mismatched credentials.
        if token_changed:
            sync_settings.write_token(previous_token)
        raise
    ctx.sync_configuration_changed()
    return ctx.sync_status()


@command("sync.now", ACTION, ("sync",))
def sync_now(ctx, params):
    return {"started": ctx.request_sync(), "status": ctx.sync_status()}


# ── lifecycle ──────────────────────────────────────────────────────────
@command("app.foreground", ACTION)
def app_foreground(ctx, params):
    ctx.foreground_changed(_bool(params, "active"))
    return {}


@command("app.quit", ACTION)
def app_quit(ctx, params):
    return ctx.request_quit()


def describe() -> list[dict[str, Any]]:
    return [
        {"name": item.name, "kind": item.kind, "topics": list(item.topics)}
        for item in sorted(REGISTRY.values(), key=lambda c: c.name)
    ]
