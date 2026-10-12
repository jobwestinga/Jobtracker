import { $, el } from "../../shared/dom.js";
import { openSheet, createSheetAction, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";

export function createGoalsFeature({ store, act, banner, render, getView, reorderButtons }) {
  const state = { goalsFilter: "active", expandedGoal: null };
  let milestoneIndex = { source: null, map: new Map() };
  const milestoneDrafts = new Map();
  const milestoneRevisions = new Map();
  const milestoneSaves = new Set();
  const pendingWrites = new Set();
  let epoch = 0;

  function validText(input, message) {
    input.setCustomValidity(input.value.trim() ? "" : message);
    return input.reportValidity();
  }
  function sheetAction(content) {
    return createSheetAction(content, { act, onError: (error) => banner(error.message || "That did not work") });
  }
  async function inlineWrite(key, op, params, options) {
    if (pendingWrites.has(key)) return;
    const owner = epoch;
    pendingWrites.add(key);
    try { return await act(op, params, options); }
    catch (error) { banner(error.message || "That did not work"); return { ok: false }; }
    finally {
      if (owner === epoch) {
        pendingWrites.delete(key);
        if (getView() === "goals") renderGoals();
      }
    }
  }

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
    const focusKey = focused?.dataset?.goalFocus;
    const selection = focusGoal ? [focused.selectionStart, focused.selectionEnd] : null;
    $("goals-active").classList.toggle("on", state.goalsFilter === "active");
    $("goals-done").classList.toggle("on", state.goalsFilter === "done");
    $("goals-active").setAttribute("aria-pressed", String(state.goalsFilter === "active"));
    $("goals-done").setAttribute("aria-pressed", String(state.goalsFilter === "done"));

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
    } else if (focusKey) {
      const control = [...list.querySelectorAll("[data-goal-focus]")].find((element) => element.dataset.goalFocus === focusKey);
      const target = control?.disabled ? control.closest(".goal-card")?.querySelector('[data-goal-focus^="expand:"]') : control;
      (target || $(state.goalsFilter === "done" ? "goals-done" : "goals-active")).focus({ preventScroll: true });
    }
  }

  function goalCard(goal) {
    const { done, total, items } = goalProgress(goal.uid);
    const expanded = state.expandedGoal === goal.uid;
    const wrap = el("div", "card goal-card");

    const head = el("div", "goal-head");
    const check = el("button", "check", "✓");
    check.setAttribute("aria-label", `${goal.is_completed ? "Reopen" : "Complete"} ${goal.name}`);
    check.setAttribute("aria-pressed", String(Boolean(goal.is_completed)));
    check.dataset.goalFocus = `complete:${goal.uid}`;
    check.disabled = pendingWrites.has(`complete:${goal.uid}`);
    const blocked = total > 0 && done < total && !goal.is_completed;
    if (goal.is_completed) check.classList.add("done");
    if (blocked) check.classList.add("blocked");
    check.onclick = (event) => {
      event.stopPropagation();
      if (goal.is_completed) {
        return inlineWrite(`complete:${goal.uid}`, "uncomplete_goal", { goal_uid: goal.uid },
          { optimistic: () => patchGoal(goal.uid, { is_completed: 0 }) });
      }
      if (blocked) return banner(`${total - done} milestone(s) still to tick`);
      return inlineWrite(`complete:${goal.uid}`, "complete_goal", { goal_uid: goal.uid },
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
    body.setAttribute("role", "button");
    body.setAttribute("tabindex", "0");
    body.setAttribute("aria-expanded", String(expanded));
    if (expanded) body.setAttribute("aria-controls", `goal-panel-${goal.uid}`);
    body.setAttribute("aria-label", `${goal.name}${total ? `, ${done} of ${total} milestones complete` : ""}`);
    body.dataset.goalFocus = `expand:${goal.uid}`;
    body.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); body.onclick(); }
    };

    const star = el("button", `star ${goal.is_focused ? "on" : ""}`, goal.is_focused ? "★" : "☆");
    star.setAttribute("aria-label", `${goal.is_focused ? "Unfocus" : "Focus"} ${goal.name}`);
    star.setAttribute("aria-pressed", String(Boolean(goal.is_focused)));
    star.dataset.goalFocus = `focus:${goal.uid}`;
    star.disabled = pendingWrites.has(`focus:${goal.uid}`);
    star.onclick = (event) => {
      event.stopPropagation();
      return inlineWrite(`focus:${goal.uid}`, "toggle_goal_focused", { goal_uid: goal.uid },
        { optimistic: () => patchGoal(goal.uid, { is_focused: goal.is_focused ? 0 : 1 }) });
    };

    const edit = el("button", "icon-btn", "⋯");
    edit.setAttribute("aria-label", `Edit ${goal.name}`);
    edit.dataset.goalFocus = `edit:${goal.uid}`;
    edit.onclick = (event) => { event.stopPropagation(); goalSheet(goal); };
    head.append(check, body, star, edit);
    wrap.append(head);

    if (expanded) {
      const panel = el("div", "goal-panel");
      panel.id = `goal-panel-${goal.uid}`;
      if (goal.notes) panel.append(el("p", "muted goal-notes", goal.notes));

      for (const milestone of items) {
        const row = el("div", "ms-row");
        const box = el("button", `check small ${milestone.is_done ? "done" : ""}`, "✓");
        box.setAttribute("aria-label", `${milestone.is_done ? "Reopen" : "Complete"} milestone ${milestone.title}`);
        box.setAttribute("aria-pressed", String(Boolean(milestone.is_done)));
        box.dataset.goalFocus = `milestone-check:${milestone.uid}`;
        box.disabled = pendingWrites.has(`milestone:${milestone.uid}`);
        const label = el("button", "grow milestone-title", milestone.title);
        label.disabled = box.disabled;
        label.setAttribute("aria-pressed", String(Boolean(milestone.is_done)));
        label.dataset.goalFocus = `milestone-title:${milestone.uid}`;
        if (milestone.is_done) label.classList.add("struck");
        const toggle = () =>
          inlineWrite(`milestone:${milestone.uid}`, "set_milestone_done", { milestone_uid: milestone.uid, done: !milestone.is_done },
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
      input.oninput = () => {
        input.setCustomValidity("");
        milestoneDrafts.set(goal.uid, input.value);
        milestoneRevisions.set(goal.uid, (milestoneRevisions.get(goal.uid) || 0) + 1);
      };
      const addBtn = el("button", "secondary", "+");
      addBtn.setAttribute("aria-label", "Add milestone");
      addBtn.disabled = milestoneSaves.has(goal.uid);
      addBtn.onclick = async () => {
        const title = input.value.trim();
        if (!input.isConnected || milestoneSaves.has(goal.uid) || !validText(input, "Enter a milestone title.")) return;
        const owner = epoch, revision = milestoneRevisions.get(goal.uid) || 0;
        milestoneSaves.add(goal.uid); addBtn.disabled = true;
        try {
          const outcome = await act("add_milestone", { goal_uid: goal.uid, title }, { uid: uuid() });
          if (owner === epoch && (outcome?.ok || outcome?.queued) && (milestoneRevisions.get(goal.uid) || 0) === revision) {
            milestoneDrafts.delete(goal.uid);
          }
        } catch (error) { if (owner === epoch) banner(error.message || "That did not work"); }
        finally {
          if (owner === epoch) {
            milestoneSaves.delete(goal.uid);
            if (getView() === "goals") renderGoals();
          }
        }
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
    openSheet(goal.name, (host) => {
      const body = el("div"); host.append(body);
      const submit = sheetAction(body);
      const name = field(body, "Title",
        Object.assign(document.createElement("input"), { type: "text", required: true, value: goal.name }));
      name.oninput = () => name.setCustomValidity("");
      const notes = field(body, "Description",
        Object.assign(document.createElement("textarea"), { rows: 3, value: goal.notes || "" }));
      const deadline = field(body, "Deadline (optional)",
        Object.assign(document.createElement("input"), { type: "date", value: goal.deadline || "" }));

      const { items } = goalProgress(goal.uid);
      body.append(el("label", null, "Milestones"));
      for (const milestone of items) {
        const row = el("div", "ms-row");
        const check = el("button", `check ${milestone.is_done ? "done" : ""}`, "✓");
        check.setAttribute("aria-label", `${milestone.is_done ? "Reopen" : "Complete"} milestone ${milestone.title}`);
        check.onclick = async () => {
          await submit("set_milestone_done",
            { milestone_uid: milestone.uid, done: !milestone.is_done });
        };
        const label = el("div", "grow", milestone.title);
        const del = el("button", "icon-btn", "✕");
        del.setAttribute("aria-label", `Delete milestone ${milestone.title}`);
        del.onclick = async () => {
          await submit("delete_milestone", { milestone_uid: milestone.uid });
        };
        row.append(check, label, del);
        body.append(row);
      }

      const addRow = el("div", "row");
      const newMilestone = Object.assign(document.createElement("input"),
        { type: "text", placeholder: "New milestone" });
      newMilestone.setAttribute("aria-label", "New milestone");
      newMilestone.oninput = () => newMilestone.setCustomValidity("");
      const addBtn = el("button", "secondary", "Add");
      addBtn.onclick = async () => {
        const title = newMilestone.value.trim();
        if (!submit.canSubmit() || !validText(newMilestone, "Enter a milestone title.")) return;
        await submit("add_milestone", { goal_uid: goal.uid, title }, { uid: uuid() });
      };
      addRow.append(newMilestone, addBtn);
      body.append(addRow);

      const save = el("button", "primary wide", "Save changes");
      save.onclick = async () => {
        if (!submit.canSubmit() || !validText(name, "Enter a goal title.") || !deadline.reportValidity()) return;
        await submit("update_goal", {
          goal_uid: goal.uid,
          name: name.value.trim(),
          notes: notes.value,
          deadline: deadline.value || null,
        });
      };
      body.append(save);
      reorderButtons(body, (store.snapshot?.goals || []).filter((row) => Boolean(row.is_completed) === Boolean(goal.is_completed)),
        goal.uid, "set_goal_order", { completed: Boolean(goal.is_completed) }, submit);

      const del = el("button", "danger wide", "Delete goal");
      del.onclick = async () => {
        if (!submit.canSubmit()) return;
        if (!confirm(`Delete "${goal.name}"?`)) return;
        await submit("delete_goal", { goal_uid: goal.uid });
      };
      body.append(del);
    });
  }

  function addGoalSheet() {
    openSheet("New goal", (host) => {
      const body = el("div"); host.append(body);
      const submit = sheetAction(body);
      const name = field(body, "Title",
        Object.assign(document.createElement("input"), { type: "text", required: true, placeholder: "What outcome?" }));
      name.oninput = () => name.setCustomValidity("");
      const notes = field(body, "Description (optional)",
        Object.assign(document.createElement("textarea"), { rows: 3 }));
      const deadline = field(body, "Deadline (optional)",
        Object.assign(document.createElement("input"), { type: "date" }));
      const save = el("button", "primary wide", "Add goal");
      save.onclick = async () => {
        const title = name.value.trim();
        if (!submit.canSubmit() || !validText(name, "Enter a goal title.") || !deadline.reportValidity()) return;
        await submit("add_goal", { name: title, notes: notes.value, deadline: deadline.value || null }, { uid: uuid() });
      };
      body.append(save);
    });
  }

  function mount() {
    $("add-goal").onclick = addGoalSheet;
    $("goals-active").onclick = () => { state.goalsFilter = "active"; render(); };
    $("goals-done").onclick = () => { state.goalsFilter = "done"; render(); };
  }
  function reset() {
    epoch++;
    milestoneDrafts.clear(); milestoneRevisions.clear(); milestoneSaves.clear(); pendingWrites.clear();
    milestoneIndex = { source: null, map: new Map() }; state.expandedGoal = null;
  }
  return { mount, render: renderGoals, reset };
}
