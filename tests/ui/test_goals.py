"""Offscreen native goals regressions on isolated databases."""
from PySide6.QtCore import (Qt)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QCheckBox, QGraphicsOpacityEffect, QLabel, QMessageBox)
from jobtracker.ui.widgets import (goal_dialog as goal_dialog_module)
from jobtracker.ui.widgets.dialog_utils import (open_dialog)
from jobtracker.ui.widgets.goal_dialog import (GoalDialog, GoalDetailDialog, apply_goal_edits)
from jobtracker.ui.widgets.subject_dialog import (SubjectDialog)
from jobtracker.ui.widgets.reorderable_list import (ReorderableCardList)
from jobtracker.ui.widgets.template_dialog import (TemplateDialog, TemplateManagerDialog)
from jobtracker.ui.widgets.todo_task_item import (TodoTaskItemWidget)
from jobtracker.ui.widgets.todo_task_item import (_StarBurstOverlay)
from tests.support.native import (_application, _window)

def test_one_level_undo_restores_subject_archive_order_and_milestone(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        first = window.service.add_subject("First", "#111111", "")
        second = window.service.add_subject("Second", "#222222", "")
        window._archive_subject(first.id)
        assert [s.id for s in window.service.get_all_subjects()] == [second.id]
        window.activateWindow()
        window.setFocus()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
        assert [s.id for s in window.service.get_all_subjects()] == [
            first.id,
            second.id,
        ]

        window._on_subject_order_changed([second.id, first.id])
        assert [s.id for s in window.service.get_all_subjects()] == [
            second.id,
            first.id,
        ]
        window._perform_undo(force=True)
        assert [s.id for s in window.service.get_all_subjects()] == [
            first.id,
            second.id,
        ]

        goal = window.service.add_todo_task("Undo goal", "", None)
        milestone = window.service.add_milestone(goal.id, "Undo milestone")
        detail = GoalDetailDialog(window.service, goal.id, window)
        open_dialog(detail)
        qt_app.processEvents()
        key_target = detail._milestone_checks[0]
        key_target.setFocus()
        qt_app.processEvents()
        QTest.keyClick(key_target, Qt.Key_1)
        assert window.service.db.get_milestone(milestone.id).is_done == 1
        QTest.keyClick(key_target, Qt.Key_Z, Qt.ControlModifier)
        assert window.service.db.get_milestone(milestone.id).is_done == 0
        detail.close()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_clicking_goal_card_emits_open_request(service):
    qt_app = _application()
    goal = service.add_todo_task("Open me", "", None)
    card = TodoTaskItemWidget(goal, {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa",
                                     "TEXT_DIMMED": "#777", "ACCENT_GREEN": "#0f0",
                                     "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
                                     "ACCENT": "#38f"})
    opened = []
    card.open_requested.connect(opened.append)
    card.resize(500, 90)
    card.show()
    qt_app.processEvents()
    QTest.mouseClick(card, Qt.LeftButton, pos=card.rect().center())
    assert opened == [goal.id]
    card.close()


def test_check_button_gated_by_milestone_progress(service):
    qt_app = _application()
    tokens = {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa", "TEXT_DIMMED": "#777",
              "ACCENT_GREEN": "#0f0", "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
              "ACCENT": "#38f"}
    goal = service.add_todo_task("Gated", "", None)

    # 1/2 milestones done -> completion blocked; ✓ shakes, does not emit.
    blocked = TodoTaskItemWidget(goal, tokens, progress=(1, 2))
    fired = []
    blocked.complete_requested.connect(fired.append)
    blocked.show()
    qt_app.processEvents()
    blocked.check_btn.click()
    assert fired == []
    blocked.close()

    # All milestones done -> ✓ emits completion.
    ready = TodoTaskItemWidget(goal, tokens, progress=(2, 2))
    fired_ok = []
    ready.complete_requested.connect(fired_ok.append)
    ready.show()
    qt_app.processEvents()
    ready.check_btn.click()
    assert fired_ok == [goal.id]
    ready.close()


def test_goal_removal_fade_is_retained_until_completion():
    qt_app = _application()
    cards = ReorderableCardList()
    card = QLabel("Completing")
    card.setFixedHeight(60)
    cards.add_card(1, card)
    cards.resize(400, 180)
    cards.show()
    qt_app.processEvents()

    cards.animate_remove(1)

    assert 1 in cards._remove_anims
    assert isinstance(card.graphicsEffect(), QGraphicsOpacityEffect)
    cards._remove_anims[1].stop()
    cards.close()


def test_completed_goal_card_can_only_be_reopened_from_detail(service):
    qt_app = _application()
    goal = service.add_todo_task("Restore me", "", None)
    assert service.complete_goal(goal.id)
    completed = service.get_goal(goal.id)
    card = TodoTaskItemWidget(completed, {"TEXT_PRIMARY": "#fff", "TEXT_SECONDARY": "#aaa",
                                          "TEXT_DIMMED": "#777", "ACCENT_GREEN": "#0f0",
                                          "BORDER_COLOR": "#333", "BG_SECONDARY": "#111",
                                          "ACCENT": "#38f"})
    card.show()
    qt_app.processEvents()
    # Completed cards hide the check button entirely; clicking does nothing.
    assert card.check_btn.isHidden()
    card.check_btn.click()

    # Reopen is only reachable from the detail dialog (now labelled "Reopen Goal").
    detail = GoalDetailDialog(service, goal.id)
    assert detail.complete_btn.text() == "Reopen Goal"
    detail.complete_btn.click()
    assert not service.get_goal(goal.id).is_completed
    detail.close()
    card.close()


def test_goal_edits_are_the_only_place_that_adds_or_deletes_milestones(service):
    qt_app = _application()
    goal = service.add_todo_task("Original", "Old motivation", None)
    first = service.add_milestone(goal.id, "Keep", "Old description")
    service.add_milestone(goal.id, "Remove")

    apply_goal_edits(
        service,
        goal.id,
        {
            "name": "Updated",
            "notes": "New motivation",
            "milestones": [
                {
                    "id": first.id,
                    "title": "Renamed",
                    "note": "Updated description",
                },
                {
                    "id": None,
                    "title": "Added",
                    "note": "New description",
                },
            ],
        },
    )
    assert service.get_goal(goal.id).name == "Updated"
    assert [m.title for m in service.get_goal_milestones(goal.id)] == [
        "Renamed", "Added",
    ]
    assert [m.note for m in service.get_goal_milestones(goal.id)] == [
        "Updated description", "New description",
    ]

    detail = GoalDetailDialog(service, goal.id)
    detail.show()
    qt_app.processEvents()
    assert detail.windowTitle() == "Updated"
    assert not hasattr(detail, "title_lbl")
    assert detail.desc_lbl.text() == "New motivation"
    button_texts = {button.text() for button in detail.findChildren(type(detail.edit_btn))}
    assert "Edit Goal" in button_texts
    assert "Add" not in button_texts
    assert "Remove" not in button_texts
    assert "✕" not in button_texts
    detail.close()


def test_add_milestone_button_inserts_an_editable_row():
    qt_app = _application()
    dialog = GoalDialog()
    dialog.show()
    qt_app.processEvents()
    assert len(dialog._milestone_rows) == 1

    dialog._add_milestone_btn.click()
    qt_app.processEvents()

    assert len(dialog._milestone_rows) == 2
    assert dialog._milestone_rows[-1]["input"].isVisible()
    assert dialog._milestone_rows[-1]["note_input"].isVisible()
    dialog.close()


def test_new_subject_and_goal_use_deferred_inline_panels(
    database, monkeypatch
):
    qt_app, window = _window(database, monkeypatch)
    try:
        assert window._pages.currentIndex() == 1
        window._nav_buttons[0].click()
        assert window._pages.currentIndex() == 1
        qt_app.processEvents()
        assert window._pages.currentIndex() == 0
        window._switch_page(1)

        window._new_subject()
        subject_dialog = window.findChild(SubjectDialog)
        assert subject_dialog is not None
        assert not subject_dialog.isVisible()
        qt_app.processEvents()
        assert subject_dialog.isVisible()
        assert subject_dialog.windowType() == Qt.Widget
        assert subject_dialog.window() is window
        assert not window._shortcut_focus_allows_navigation()
        subject_dialog.name_input.setText("Deferred subject")
        subject_dialog.accept()
        assert not window.service.get_all_subjects()
        qt_app.processEvents()
        assert [s.name for s in window.service.get_all_subjects()] == [
            "Deferred subject"
        ]
        qt_app.processEvents()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 0

        window._new_goal()
        goal_dialog = window.findChild(GoalDialog)
        assert goal_dialog is not None
        assert not goal_dialog.isVisible()
        qt_app.processEvents()
        assert goal_dialog.isVisible()
        assert goal_dialog.windowType() == Qt.Widget
        assert goal_dialog.window() is window
        goal_dialog.title_input.setText("Deferred goal")
        goal_dialog.accept()
        assert not window.service.get_active_goals()
        qt_app.processEvents()
        assert [g.name for g in window.service.get_active_goals()] == [
            "Deferred goal"
        ]
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_nested_goal_editor_stays_in_main_window(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        goal = window.service.add_todo_task("Nested editor", "", None)
        window._open_goal(goal.id)
        qt_app.processEvents()
        detail = window.findChild(GoalDetailDialog)
        assert detail is not None and detail.isVisible()
        assert detail.window() is window
        assert int(window.property("_jt_inline_dialog_count") or 0) == 1

        detail._edit_goal()
        qt_app.processEvents()
        editor = window.findChild(GoalDialog)
        assert editor is not None and editor.isVisible()
        assert editor.window() is window
        assert int(window.property("_jt_inline_dialog_count") or 0) == 2

        editor.reject()
        qt_app.processEvents()
        assert detail.isVisible()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 1

        detail.accept()
        qt_app.processEvents()
        assert int(window.property("_jt_inline_dialog_count") or 0) == 0
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_edit_goal_offers_confirmed_permanent_delete(service, monkeypatch):
    goal = service.add_todo_task("Delete from edit", "", None)
    dialog = GoalDialog(
        goal=goal,
        milestones=[],
    )
    assert dialog.delete_btn.text() == "Delete Goal"
    monkeypatch.setattr(
        goal_dialog_module,
        "question",
        lambda _parent, _title, _text, on_finished, **_kwargs: on_finished(
            QMessageBox.Yes
        ),
    )
    dialog._confirm_delete()
    assert dialog.result() == GoalDialog.DELETE_RESULT
    dialog.close()


def test_checking_milestone_starts_completion_animation(service):
    qt_app = _application()
    goal = service.add_todo_task("Animated", "Description", None)
    milestone = service.add_milestone(goal.id, "First", "Visible detail")
    detail = GoalDetailDialog(service, goal.id)
    detail.show()
    qt_app.processEvents()
    check = detail.findChildren(QCheckBox)[0]
    check.click()
    qt_app.processEvents()
    assert service.db.get_milestone(milestone.id).is_done == 1
    burst = detail.findChildren(_StarBurstOverlay)[0]
    assert burst._origin.x() < detail.width() / 3
    detail.close()


def test_template_manager_makes_selection_and_action_clear(service):
    qt_app = _application()
    template = service.add_goal_template("Weekly review", "", "weekly", ["Review"])
    manager = TemplateManagerDialog(service)
    manager.show()
    qt_app.processEvents()
    assert manager._list.currentRow() == 0
    assert manager._edit_btn.isEnabled()
    assert manager._toggle_btn.text() == "Disable"
    assert template.title in manager._selection_label.text()
    manager.close()


def test_template_dialog_edits_schedule_and_milestone_descriptions(service):
    qt_app = _application()
    template = service.add_goal_template(
        "Weekly review",
        "Reset priorities",
        "weekly",
        [{"title": "Review goals", "note": "Check what still matters."}],
        recurrence_day=3,
    )
    dialog = TemplateDialog(template=template)
    dialog.show()
    qt_app.processEvents()

    assert dialog.recurrence_combo.currentData() == "weekly"
    assert dialog.schedule_combo.currentData() == 3
    assert dialog._milestone_rows[0]["input"].text() == "Review goals"
    assert (
        dialog._milestone_rows[0]["note_input"].toPlainText()
        == "Check what still matters."
    )

    dialog.recurrence_combo.setCurrentIndex(
        dialog.recurrence_combo.findData("monthly")
    )
    dialog.schedule_combo.setCurrentIndex(
        dialog.schedule_combo.findData(27)
    )
    data = dialog.get_data()
    assert data["recurrence"] == "monthly"
    assert data["recurrence_day"] == 27
    assert data["milestones"] == [
        {"title": "Review goals", "note": "Check what still matters."}
    ]
    dialog.close()


def test_goal_focus_star_toggles_and_highlights(database, monkeypatch):
    qt_app, window = _window(database, monkeypatch)
    try:
        goal = window.service.add_todo_task("Weekly focus", "", None)
        window._reload_tasks()
        qt_app.processEvents()

        card = window._todo_list.card_widget(goal.id)
        assert card.focus_btn.text() == "☆"
        card.focus_btn.click()
        qt_app.processEvents()
        assert window.service.get_goal(goal.id).is_focused == 1

        # Focus is structural: the card rebuilt with accent emphasis.
        card = window._todo_list.card_widget(goal.id)
        assert card.focus_btn.text() == "★"
        assert window._tokens["ACCENT"] in card.styleSheet()

        card.focus_btn.click()
        qt_app.processEvents()
        assert window.service.get_goal(goal.id).is_focused == 0
        card = window._todo_list.card_widget(goal.id)
        assert card.focus_btn.text() == "☆"
        assert window._tokens["ACCENT"] not in card.styleSheet()
    finally:
        window._graph_live_timer.stop()
        window._heartbeat_timer.stop()
        window.close()
        qt_app.processEvents()


def test_goal_card_transient_refresh_keeps_focus_star_consistent(service):
    from jobtracker.ui.widgets.todo_task_item import TodoTaskItemWidget
    from jobtracker.core.themes import DEFAULT_TOKENS

    _application()
    goal = service.add_todo_task("Focus me", "", None)
    service.toggle_goal_focused(goal.id)
    card = TodoTaskItemWidget(service.get_goal(goal.id), DEFAULT_TOKENS)
    assert card.focus_btn.isVisibleTo(card) and card.focus_btn.text() == "★"

    # Completing through a transient refresh must hide the star, not strand it.
    service.complete_goal(goal.id)
    card.apply_transient(service.get_goal(goal.id), (0, 0))
    assert not card.focus_btn.isVisibleTo(card)
    assert DEFAULT_TOKENS["ACCENT"] not in card.styleSheet()

