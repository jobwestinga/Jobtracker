import { $, el } from "../../shared/dom.js";
import { openSheet, closeSheet, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";

export function createGoalsFeature({ store, act, banner, render, getView, reorderButtons }) {
  const state = { goalsFilter: "active", expandedGoal: null };
  let milestoneIndex = { source: null, map: new Map() };
  const milestoneDrafts = new Map();
  const milestoneSaves = new Set();

  function patchGoal(uid, changes) {
    const goal = (store.snapshot?.goals || []).find((g) => g.uid === uid);
    if (goal) Object.assign(goal, changes);
  }

  function patchMilestone(uid, changes) {
    const milestone = (store.snapshot?.milestones || []).find((m) => m.uid === uid);
    if (milestone) Object.assign(milestone, changes);
  }

  function milestonesByGoal() {
    const milestones = store.snapshot?.milestones || [];
    if (milestoneIndex.source !== milestones) {
      const map = new Map();
      for (const milestone of milestones) {
        const list = map.get(milestone.goal_uid);
        if (list) list.push(milestone);
        else map.set(milestone.goal_uid, [milestone]);
      }
      milestoneIndex = { source: milestones, map };
    }
    return milestoneIndex.map;
  }

  function goalProgress(goalUid) {
    const all = milestonesByGoal().get(goalUid) || [];
    let done = 0;
    for (const milestone of all) if (milestone.is_done) done += 1;
    return { done, total: all.length, items: all };
  }

  function renderGoals() {
    const focused = document.activeElement;
    const focusGoal = focused?.dataset?.milestoneGoal;
    const selection = focusGoal ? [focused.selectionStart, focused.selectionEnd] : null;
    $("goals-active").classList.toggle("on", state.goalsFilter === "active");
    $("goals-done").classList.toggle("on", state.goalsFilter === "done");

    const list = $("goal-list");
    list.innerHTML = "";
    const goals = (store.snapshot?.goals || [])
      .filter((g) => (state.goalsFilter === "done" ? g.is_completed : !g.is_completed))
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

    if (!goals.length) {
      list.append(el("p", "muted",
        state.goalsFilter === "done" ? "No completed goals yet." : "No goals yet."));
      return;
    }

    for (const goal of goals) {
      list.append(goalCard(goal));
    }
    if (focusGoal) {
      const input = [...list.querySelectorAll("input[data-milestone-goal]")]
        .find((item) => item.dataset.milestoneGoal === focusGoal);
      if (input) {
        input.focus({ preventScroll: true });
        input.setSelectionRange(...selection);
      }
    }
  }

  function goalCard(goal) {
    const { done, total, items } = goalProgress(goal.uid);
    const expanded = state.expandedGoal === goal.uid;
    const wrap = el("div", "card goal-card");

    const head = el("div", "goal-head");
    const check = el("button", "check", "✓");
    check.setAttribute("aria-label", `${goal.is_completed ? "Reopen" : "Complete"} ${goal.name}`);
    const blocked = total > 0 && done < total && !goal.is_completed;
    if (goal.is_completed) check.classList.add("done");
    if (blocked) check.classList.add("blocked");
    check.onclick = (event) => {
      event.stopPropagation();
      if (goal.is_completed) {
        return act("uncomplete_goal", { goal_uid: goal.uid },
          { optimistic: () => patchGoal(goal.uid, { is_completed: 0 }) });
      }
      if (blocked) return banner(`${total - done} milestone(s) still to tick`);
      return act("complete_goal", { goal_uid: goal.uid },
        { optimistic: () => patchGoal(goal.uid, { is_completed: 1 }) });
    };

    const body = el("div", "grow");
    body.append(el("div", "name", goal.name));
    if (total) {
      body.append(el("div", "sub", `${done}/${total} milestones`));
      const bar = el("div", "bar");
      const fill = el("i");
      fill.style.width = `${(done / total) * 100}%`;
      bar.append(fill);
      body.append(bar);
    } else if (goal.notes) {
      body.append(el("div", "sub", goal.notes));
    }
    // Tapping the goal expands it rather than opening an editor.
    body.onclick = () => {
      state.expandedGoal = expanded ? null : goal.uid;
      render();
    };

    const star = el("button", `star ${goal.is_focused ? "on" : ""}`, goal.is_focused ? "★" : "☆");
    star.setAttribute("aria-label", `${goal.is_focused ? "Unfocus" : "Focus"} ${goal.name}`);
    star.onclick = (event) => {
      event.stopPropagation();
      act("toggle_goal_focused", { goal_uid: goal.uid },
        { optimistic: () => patchGoal(goal.uid, { is_focused: goal.is_focused ? 0 : 1 }) });
    };

    const edit = el("button", "icon-btn", "⋯");
    edit.setAttribute("aria-label", `Edit ${goal.name}`);
    edit.onclick = (event) => { event.stopPropagation(); goalSheet(goal); };
    head.append(check, body, star, edit);
    wrap.append(head);

    if (expanded) {
      const panel = el("div", "goal-panel");
      if (goal.notes) panel.append(el("p", "muted goal-notes", goal.notes));

      for (const milestone of items) {
        const row = el("div", "ms-row");
        const box = el("button", `check small ${milestone.is_done ? "done" : ""}`, "✓");
        box.setAttribute("aria-label", `${milestone.is_done ? "Reopen" : "Complete"} milestone ${milestone.title}`);
        const label = el("div", "grow", milestone.title);
        if (milestone.is_done) label.classList.add("struck");
        const toggle = () =>
          act("set_milestone_done", { milestone_uid: milestone.uid, done: !milestone.is_done },
            { optimistic: () => patchMilestone(milestone.uid, { is_done: milestone.is_done ? 0 : 1 }) });
        box.onclick = toggle;
        label.onclick = toggle;
        row.append(box, label);
        panel.append(row);
      }

      const add = el("div", "row");
      const input = Object.assign(document.createElement("input"),
        { type: "text", placeholder: "Add milestone", value: milestoneDrafts.get(goal.uid) || "" });
      input.dataset.milestoneGoal = goal.uid;
      input.setAttribute("aria-label", `Add milestone to ${goal.name}`);
      input.oninput = () => milestoneDrafts.set(goal.uid, input.value);
      const addBtn = el("button", "secondary", "+");
      addBtn.setAttribute("aria-label", "Add milestone");
      addBtn.disabled = milestoneSaves.has(goal.uid);
      addBtn.onclick = async () => {
        const title = input.value.trim();
        if (!title || milestoneSaves.has(goal.uid)) return;
        milestoneSaves.add(goal.uid); addBtn.disabled = true;
        const outcome = await act("add_milestone", { goal_uid: goal.uid, title }, { uid: uuid() });
        if ((outcome.ok || outcome.queued) && (milestoneDrafts.get(goal.uid) || "").trim() === title) {
          milestoneDrafts.delete(goal.uid);
        }
        milestoneSaves.delete(goal.uid);
        if (getView() === "goals") renderGoals();
      };
      input.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); addBtn.onclick(); } };
      add.append(input, addBtn);
      panel.append(add);

      const more = el("button", "ghost wide", "⋯ Edit or delete this goal");
      more.onclick = () => goalSheet(goal);
      panel.append(more);
      wrap.append(panel);
    }

    return wrap;
  }

  function goalSheet(goal) {
    openSheet(goal.name, (body) => {
      const name = field(body, "Title",
        Object.assign(document.createElement("input"), { type: "text", value: goal.name }));
      const notes = field(body, "Description",
        Object.assign(document.createElement("textarea"), { rows: 3, value: goal.notes || "" }));
      const deadline = field(body, "Deadline (optional)",
        Object.assign(document.createElement("input"), { type: "date", value: goal.deadline || "" }));

      const { items } = goalProgress(goal.uid);
      body.append(el("label", null, "Milestones"));
      for (const milestone of items) {
        const row = el("div", "ms-row");
        const check = el("button", `check ${milestone.is_done ? "done" : ""}`, "✓");
        check.onclick = async () => {
          closeSheet();
          await act("set_milestone_done",
            { milestone_uid: milestone.uid, done: !milestone.is_done });
        };
        const label = el("div", "grow", milestone.title);
        const del = el("button", "icon-btn", "✕");
        del.onclick = async () => {
          closeSheet();
          await act("delete_milestone", { milestone_uid: milestone.uid });
        };
        row.append(check, label, del);
        body.append(row);
      }

      const addRow = el("div", "row");
      const newMilestone = Object.assign(document.createElement("input"),
        { type: "text", placeholder: "New milestone" });
      const addBtn = el("button", "secondary", "Add");
      addBtn.onclick = async () => {
        const title = newMilestone.value.trim();
        if (!title) return;
        closeSheet();
        await act("add_milestone", { goal_uid: goal.uid, title }, { uid: uuid() });
      };
      addRow.append(newMilestone, addBtn);
      body.append(addRow);

      const save = el("button", "primary wide", "Save changes");
      save.onclick = async () => {
        closeSheet();
        await act("update_goal", {
          goal_uid: goal.uid,
          name: name.value.trim() || goal.name,
          notes: notes.value,
          deadline: deadline.value || null,
        });
      };
      body.append(save);
      reorderButtons(body, (store.snapshot?.goals || []).filter((row) => Boolean(row.is_completed) === Boolean(goal.is_completed)),
        goal.uid, "set_goal_order", { completed: Boolean(goal.is_completed) });

      const del = el("button", "danger wide", "Delete goal");
      del.onclick = async () => {
        if (!confirm(`Delete "${goal.name}"?`)) return;
        closeSheet();
        await act("delete_goal", { goal_uid: goal.uid });
      };
      body.append(del);
    });
  }

  function addGoalSheet() {
    openSheet("New goal", (body) => {
      const name = field(body, "Title",
        Object.assign(document.createElement("input"), { type: "text", placeholder: "What outcome?" }));
      const notes = field(body, "Description (optional)",
        Object.assign(document.createElement("textarea"), { rows: 3 }));
      const deadline = field(body, "Deadline (optional)",
        Object.assign(document.createElement("input"), { type: "date" }));
      const save = el("button", "primary wide", "Add goal");
      save.onclick = async () => {
        const title = name.value.trim();
        if (!title) return;
        closeSheet();
        await act("add_goal", { name: title, notes: notes.value, deadline: deadline.value || null }, { uid: uuid() });
      };
      body.append(save);
      setTimeout(() => name.focus(), 50);
    });
  }

  function mount() {
    $("add-goal").onclick = addGoalSheet;
    $("goals-active").onclick = () => { state.goalsFilter = "active"; render(); };
    $("goals-done").onclick = () => { state.goalsFilter = "done"; render(); };
  }
  function reset() { milestoneDrafts.clear(); state.expandedGoal = null; }
  return { mount, render: renderGoals, reset };
}
