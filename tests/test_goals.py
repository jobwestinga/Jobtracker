"""Goals: milestones, progress, milestone-gated completion, recoverability."""


def _goal(service, name="Become FIDE master"):
    return service.add_todo_task(name, "outcome-focused goal", None)


def test_create_goal(service):
    g = _goal(service)
    assert g is not None and g.id is not None
    assert service.get_goal(g.id).name == "Become FIDE master"


def test_edit_goal(service):
    g = _goal(service)
    updated = service.update_todo_task(g.id, "Earn FIDE master title", "long-term", None)
    assert updated.name == "Earn FIDE master title"
    assert updated.notes == "long-term"


def test_goal_without_milestones_can_complete(service):
    g = _goal(service)
    assert service.can_complete_goal(g.id) is True
    assert service.complete_goal(g.id) is True
    # Completed goal moves out of the active list but is recoverable.
    assert g.id not in [x.id for x in service.get_active_goals()]
    assert g.id in [x.id for x in service.get_completed_goals()]


def test_missing_goal_cannot_complete(service):
    assert service.can_complete_goal(999999) is False
    assert service.complete_goal(999999) is False


def test_milestone_progress(service):
    g = _goal(service)
    service.add_milestone(g.id, "Reach 2000 FIDE")
    service.add_milestone(g.id, "Reach 2200 FIDE")
    assert service.get_goal_progress(g.id) == (0, 2)

    ms = service.get_goal_milestones(g.id)
    service.set_milestone_done(ms[0].id, True)
    assert service.get_goal_progress(g.id) == (1, 2)


def test_cannot_complete_with_unchecked_milestones(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    service.add_milestone(g.id, "B")
    assert service.can_complete_goal(g.id) is False
    assert service.complete_goal(g.id) is False
    # Still active because completion was refused.
    assert g.id in [x.id for x in service.get_active_goals()]


def test_can_complete_when_all_milestones_done(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    service.add_milestone(g.id, "B")
    for m in service.get_goal_milestones(g.id):
        service.set_milestone_done(m.id, True)
    assert service.can_complete_goal(g.id) is True
    assert service.complete_goal(g.id) is True


def test_completed_milestones_remain_visible(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    for m in service.get_goal_milestones(g.id):
        service.set_milestone_done(m.id, True)
    service.complete_goal(g.id)
    # Milestones still present and checked after completion.
    ms = service.get_goal_milestones(g.id)
    assert len(ms) == 1 and ms[0].is_done == 1


def test_uncomplete_reopens_goal(service):
    g = _goal(service)
    service.complete_goal(g.id)
    service.uncomplete_goal(g.id)
    assert g.id in [x.id for x in service.get_active_goals()]
    assert g.id not in [x.id for x in service.get_completed_goals()]


def test_uncheck_milestone(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    m = service.get_goal_milestones(g.id)[0]
    service.set_milestone_done(m.id, True)
    assert service.get_goal_progress(g.id) == (1, 1)
    service.set_milestone_done(m.id, False)
    assert service.get_goal_progress(g.id) == (0, 1)


def test_unchecking_completed_milestone_reopens_goal(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    m = service.get_goal_milestones(g.id)[0]
    service.set_milestone_done(m.id, True)
    assert service.complete_goal(g.id) is True

    service.set_milestone_done(m.id, False)
    assert service.get_goal(g.id).is_completed == 0


def test_adding_milestone_to_completed_goal_reopens_it(service):
    g = _goal(service)
    assert service.complete_goal(g.id) is True
    service.add_milestone(g.id, "New work")
    assert service.get_goal(g.id).is_completed == 0


def test_delete_goal_cascades_milestones(service):
    g = _goal(service)
    service.add_milestone(g.id, "A")
    service.delete_todo_task(g.id)
    assert service.get_goal_milestones(g.id) == []


def test_milestone_edit(service):
    g = _goal(service)
    service.add_milestone(g.id, "Old", "n")
    m = service.get_goal_milestones(g.id)[0]
    service.update_milestone(m.id, "New title", "new note")
    updated = service.get_goal_milestones(g.id)[0]
    assert updated.title == "New title"
    assert updated.note == "new note"


def test_milestone_description_is_stored_on_create(service):
    g = _goal(service)
    created = service.add_milestone(
        g.id,
        "Publish the draft",
        "Get feedback from two reviewers before publishing.",
    )
    assert created.note == "Get feedback from two reviewers before publishing."


def test_active_and_completed_goal_orders_are_independent(service):
    active_first = _goal(service, "Active first")
    active_second = _goal(service, "Active second")
    done_first = _goal(service, "Done first")
    done_second = _goal(service, "Done second")
    service.complete_goal(done_first.id)
    service.complete_goal(done_second.id)

    service.set_todo_task_order(
        [done_second.id, done_first.id], completed=True
    )
    assert [goal.id for goal in service.get_completed_goals()] == [
        done_second.id,
        done_first.id,
    ]

    service.set_todo_task_order(
        [active_second.id, active_first.id], completed=False
    )
    assert [goal.id for goal in service.get_active_goals()] == [
        active_second.id,
        active_first.id,
    ]
    assert [goal.id for goal in service.get_completed_goals()] == [
        done_second.id,
        done_first.id,
    ]


def test_milestone_order_is_persisted(service):
    goal = _goal(service)
    first = service.add_milestone(goal.id, "First")
    second = service.add_milestone(goal.id, "Second")
    third = service.add_milestone(goal.id, "Third")

    service.set_milestone_order(
        goal.id, [third.id, first.id, second.id]
    )
    assert [
        milestone.id for milestone in service.get_goal_milestones(goal.id)
    ] == [third.id, first.id, second.id]
