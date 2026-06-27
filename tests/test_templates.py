"""Recurring goal templates: once-per-logical-period generation, no duplicates."""

from datetime import datetime

from jobtracker.core.database import Database
from jobtracker.services.tracker_service import TrackerService


def test_daily_generation_once_per_logical_day(service):
    service.add_goal_template("Daily routine", "maintenance", "daily", ["Cube", "Chess"])
    day1 = datetime(2026, 6, 20, 10, 0)

    created = service.generate_due_goal_instances(now=day1)
    assert len(created) == 1

    # Generated goal copies the template milestones.
    titles = [m.title for m in service.get_goal_milestones(created[0])]
    assert titles == ["Cube", "Chess"]


def test_daily_title_date_placeholder_uses_logical_date(service):
    service.add_goal_template(
        "Daily Skill Maintenance — {date}",
        "",
        "daily",
        [],
    )
    created = service.generate_due_goal_instances(
        now=datetime(2026, 6, 21, 2, 0)
    )
    goal = service.get_goal(created[0])
    assert goal.name == "Daily Skill Maintenance — 2026-06-20"


def test_no_duplicate_generation_same_logical_day(service):
    service.add_goal_template("Daily", "", "daily", ["X"])
    day = datetime(2026, 6, 20, 10, 0)
    assert len(service.generate_due_goal_instances(now=day)) == 1
    # Re-running the same logical day generates nothing.
    assert service.generate_due_goal_instances(now=day) == []


def test_before_3am_counts_as_previous_logical_day(service):
    service.add_goal_template("Daily", "", "daily", ["X"])
    # 23:00 generates for logical day 06-20.
    assert len(service.generate_due_goal_instances(now=datetime(2026, 6, 20, 23, 0))) == 1
    # 02:00 next calendar day is still logical day 06-20 -> no new generation.
    assert service.generate_due_goal_instances(now=datetime(2026, 6, 21, 2, 0)) == []
    # 04:00 is logical day 06-21 -> generates again.
    assert len(service.generate_due_goal_instances(now=datetime(2026, 6, 21, 4, 0))) == 1


def test_new_item_on_next_logical_day_and_old_remains(service):
    service.add_goal_template("Daily", "", "daily", ["X"])
    c1 = service.generate_due_goal_instances(now=datetime(2026, 6, 20, 10, 0))
    c2 = service.generate_due_goal_instances(now=datetime(2026, 6, 21, 10, 0))
    assert len(c1) == 1 and len(c2) == 1

    active = service.get_active_goals()
    active_ids = [g.id for g in active]
    # Both uncompleted instances remain in the list.
    assert c1[0] in active_ids and c2[0] in active_ids
    # Newest generated item sits at the top.
    assert active[0].id == c2[0]


def test_generated_item_is_normal_completable_goal(service):
    service.add_goal_template("Daily", "", "daily", [])
    gid = service.generate_due_goal_instances(now=datetime(2026, 6, 20, 10, 0))[0]
    # No milestones -> can complete manually.
    assert service.complete_goal(gid) is True


def test_weekly_generation(service):
    service.add_goal_template("Weekly review", "", "weekly", ["Review"])
    # Two different days in the same ISO week -> one generation.
    assert len(service.generate_due_goal_instances(now=datetime(2026, 6, 16, 10, 0))) == 1
    assert service.generate_due_goal_instances(now=datetime(2026, 6, 18, 10, 0)) == []
    # Next week -> generates again.
    assert len(service.generate_due_goal_instances(now=datetime(2026, 6, 23, 10, 0))) == 1


def test_weekly_generation_waits_for_selected_weekday(service):
    service.add_goal_template(
        "Wednesday review", "", "weekly", ["Review"], recurrence_day=3
    )
    assert service.generate_due_goal_instances(
        now=datetime(2026, 6, 16, 10, 0)  # Tuesday
    ) == []
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 6, 17, 3, 0)  # Wednesday at logical-day start
    )) == 1
    assert service.generate_due_goal_instances(
        now=datetime(2026, 6, 19, 10, 0)
    ) == []
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 6, 24, 10, 0)
    )) == 1


def test_monthly_generation(service):
    service.add_goal_template("Monthly admin", "", "monthly", [])
    assert len(service.generate_due_goal_instances(now=datetime(2026, 6, 5, 10, 0))) == 1
    assert service.generate_due_goal_instances(now=datetime(2026, 6, 25, 10, 0)) == []
    assert len(service.generate_due_goal_instances(now=datetime(2026, 7, 2, 10, 0))) == 1


def test_monthly_generation_waits_for_selected_day(service):
    service.add_goal_template(
        "Monthly report", "", "monthly", [], recurrence_day=27
    )
    assert service.generate_due_goal_instances(
        now=datetime(2026, 6, 26, 10, 0)
    ) == []
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 6, 27, 3, 0)
    )) == 1


def test_monthly_day_31_clamps_to_last_day(service):
    service.add_goal_template(
        "Month end", "", "monthly", [], recurrence_day=31
    )
    assert service.generate_due_goal_instances(
        now=datetime(2026, 2, 27, 10, 0)
    ) == []
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 2, 28, 10, 0)
    )) == 1


def test_template_milestone_descriptions_copy_to_generated_goal(service):
    service.add_goal_template(
        "Daily",
        "",
        "daily",
        [{"title": "Review", "note": "Check the highest-priority goals."}],
    )
    goal_id = service.generate_due_goal_instances(
        now=datetime(2026, 6, 20, 10, 0)
    )[0]
    milestones = service.get_goal_milestones(goal_id)
    assert [(m.title, m.note) for m in milestones] == [
        ("Review", "Check the highest-priority goals.")
    ]


def test_editing_template_schedule_resets_generation_period(service):
    template = service.add_goal_template(
        "Review", "", "weekly", [], recurrence_day=1
    )
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 6, 15, 10, 0)
    )) == 1
    unchanged = service.update_goal_template(
        template.id, "Review", "", "weekly", [], recurrence_day=1
    )
    assert unchanged.last_generated == "2026-06-15"
    changed = service.update_goal_template(
        template.id, "Review", "", "weekly", [], recurrence_day=3
    )
    assert changed.last_generated is None
    assert service.generate_due_goal_instances(
        now=datetime(2026, 6, 16, 10, 0)
    ) == []
    assert len(service.generate_due_goal_instances(
        now=datetime(2026, 6, 17, 10, 0)
    )) == 1


def test_inactive_template_not_generated(service):
    tpl = service.add_goal_template("Off", "", "daily", [])
    service.set_goal_template_active(tpl.id, False)
    assert service.generate_due_goal_instances(now=datetime(2026, 6, 20, 10, 0)) == []


def test_generated_goal_stays_at_top_after_database_reopen(tmp_path):
    path = tmp_path / "reopen.db"
    first_db = Database(path)
    first = TrackerService(first_db)
    old = first.add_todo_task("Existing", "", None)
    first.add_goal_template("Daily", "", "daily", ["X"])
    generated_id = first.generate_due_goal_instances(
        now=datetime(2026, 6, 20, 10, 0)
    )[0]
    assert [g.id for g in first.get_active_goals()] == [generated_id, old.id]
    first_db.connection.close()

    second_db = Database(path)
    try:
        second = TrackerService(second_db)
        assert [g.id for g in second.get_active_goals()] == [generated_id, old.id]
    finally:
        second_db.connection.close()
