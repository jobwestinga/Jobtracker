"""Goal widget selection is a read-only projection, independent of Qt."""
from dataclasses import replace

from jobtracker.core.goal_widget import deadline_label, select_goals
from jobtracker.core.models import TodoTask


def goal(name, **values):
    return TodoTask(id=1, name=name, notes="private notes", deadline=values.get("deadline"),
                    is_completed=values.get("completed", 0), sort_order=values.get("order", 0),
                    created_at=values.get("created", "2026-11-01 12:00:00"),
                    is_focused=values.get("focused", 0), uid=name)


def test_focus_then_deadline_then_recent_without_reordering_the_input():
    source = [goal("recent", created="2026-11-03T12:00:00"),
              goal("focused-b", focused=1), goal("later", deadline="2026-11-10"),
              goal("focused-a", focused=1), goal("overdue", deadline="2026-11-01"),
              goal("done", focused=1, completed=1)]
    before = [replace(g) for g in source]
    assert [g.name for g in select_goals(source)] == ["focused-b", "focused-a", "overdue", "later"]
    assert source == before


def test_recency_is_creation_time_with_stable_ties_and_legacy_values():
    rows = [goal("invalid", created="bad", deadline="bad"), goal("older", created="2026-10-01"),
            goal("newest", created="2026-11-03T01:00:00"), goal("same", created="2026-11-03 01:00:00")]
    assert [g.name for g in select_goals(rows)] == ["newest", "same", "older", "invalid"]
    assert deadline_label("2026-11-03") == "03 Nov 2026"
    assert deadline_label("invalid") is None
    assert deadline_label("2026-11-03T12:00:00+01:00") is None
    assert select_goals(rows, 0) == []
    assert select_goals([]) == []
