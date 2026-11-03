"""Read-only goal selection for the Mac desktop widget.

The widget has four rows. Focus keeps the app's existing order; remaining rows
go to dated goals (earliest deadline first), then newly created goals. This
projection never changes stored order, completion, recurrence or focus.
"""
from datetime import datetime

from . import timeutils


def _moment(value):
    parsed = timeutils.parse_iso(value) if isinstance(value, str) else None
    return parsed if parsed is not None and timeutils.is_local_datetime(parsed) else None


def select_goals(goals, limit=4):
    active = [goal for goal in goals if not goal.is_completed]
    focused = [goal for goal in active if goal.is_focused]
    others = [goal for goal in active if not goal.is_focused]
    dated = sorted((goal for goal in others if _moment(goal.deadline) is not None),
                   key=lambda goal: _moment(goal.deadline))
    recent = sorted((goal for goal in others if _moment(goal.deadline) is None),
                    key=lambda goal: _moment(goal.created_at) or datetime.min, reverse=True)
    return (focused + dated + recent)[:max(0, limit)]


def deadline_label(value):
    parsed = _moment(value)
    return parsed.strftime("%d %b %Y") if parsed is not None else None
