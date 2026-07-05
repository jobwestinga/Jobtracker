"""Weekly-focus flag on goals: toggle, persistence, backup round-trip."""

from jobtracker.core.database import Database


def test_new_goal_starts_unfocused(service):
    goal = service.add_todo_task("Focus me", "", None)
    assert service.get_goal(goal.id).is_focused == 0


def test_toggle_goal_focused_roundtrip(service):
    goal = service.add_todo_task("Focus me", "", None)
    assert service.toggle_goal_focused(goal.id) is True
    assert service.get_goal(goal.id).is_focused == 1
    assert service.toggle_goal_focused(goal.id) is False
    assert service.get_goal(goal.id).is_focused == 0


def test_toggle_missing_goal_is_refused(service):
    assert service.toggle_goal_focused(999_999) is False


def test_focus_never_affects_completion_rules(service):
    goal = service.add_todo_task("Focused goal", "", None)
    service.add_milestone(goal.id, "Step 1")
    service.toggle_goal_focused(goal.id)
    # Unchecked milestone still blocks completion, focused or not.
    assert service.can_complete_goal(goal.id) is False


def test_focus_flag_survives_backup_roundtrip(tmp_path, database, service):
    goal = service.add_todo_task("Weekly focus", "", None)
    service.toggle_goal_focused(goal.id)
    data = database.export_data()

    other = Database(tmp_path / "restored.db")
    try:
        other.import_data(data)
        restored = [
            t for t in other.get_all_todo_tasks() if t.name == "Weekly focus"
        ]
        assert restored and restored[0].is_focused == 1
    finally:
        other.connection.close()
