"""Explicit builtin operation registration, with no import-time discovery.

Domain handlers describe use cases only. server.ops owns the mutable registry,
ordered dispatch, request identity, transaction scope and replay receipts.
"""

from __future__ import annotations


def register_operations(register) -> None:
    from . import backup, goals, milestones, sessions, settings, subjects, templates, timer, weight

    register("add_subject")(subjects.add_subject)
    register("update_subject")(subjects.update_subject)
    register("archive_subject")(subjects.archive_subject)
    register("unarchive_subject")(subjects.unarchive_subject)
    register("delete_subject")(subjects.delete_subject)
    register("set_subject_order")(subjects.set_subject_order)
    register("start_subject")(timer.start_subject)
    register("stop_active_subject")(timer.stop_active_subject)
    register("switch_subject")(timer.switch_subject)
    register("heartbeat")(timer.heartbeat)
    register("resolve_recovery")(timer.resolve_recovery)
    register("add_session")(sessions.add_session)
    register("update_session")(sessions.update_session)
    register("delete_session")(sessions.delete_session)
    register("duplicate_session")(sessions.duplicate_session)
    register("shift_session")(sessions.shift_session)
    register("add_goal")(goals.add_goal)
    register("update_goal")(goals.update_goal)
    register("delete_goal")(goals.delete_goal)
    register("complete_goal")(goals.complete_goal)
    register("uncomplete_goal")(goals.uncomplete_goal)
    register("toggle_goal_focused")(goals.toggle_goal_focused)
    register("set_goal_order")(goals.set_goal_order)
    register("add_milestone")(milestones.add_milestone)
    register("update_milestone")(milestones.update_milestone)
    register("set_milestone_done")(milestones.set_milestone_done)
    register("delete_milestone")(milestones.delete_milestone)
    register("set_milestone_order")(milestones.set_milestone_order)
    register("add_template")(templates.add_template)
    register("update_template")(templates.update_template)
    register("set_template_active")(templates.set_template_active)
    register("delete_template")(templates.delete_template)
    register("generate_due_goals")(templates.generate_due_goals)
    register("set_setting")(settings.set_setting)
    register("upsert_weight")(weight.upsert_weight)
    register("delete_weight")(weight.delete_weight)
    register("import_weights")(weight.import_weights)
    register("import_backup")(backup.import_backup)
