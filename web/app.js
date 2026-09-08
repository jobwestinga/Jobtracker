// JobTracker on the phone.
//
// Deliberately plain ES modules — no framework, no bundler, nothing to install
// to change a line. It renders what the server computes and never re-implements
// a rule: the logical day, the milestone gate and the sub-30-second rule all
// stay on the server, where the desktop app's own code enforces them.

import { api, send, flush, pendingCount, getToken, setToken, ApiError, uuid } from "./api.js";

const $ = (id) => document.getElementById(id);
const state = {
  view: "today",
  snapshot: null,
  context: null,
  active: null,
  day: null,
  daySessions: [],
  goalsFilter: "active",
  tick: null,
};

// ── helpers ─────────────────────────────────────────────────────────────

const pad = (n) => String(n).padStart(2, "0");

function hms(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}
function hm(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${pad(m)}m` : `${m}m`;
}
function clockOf(iso) {
  return iso ? iso.slice(11, 16) : "--:--";
}
function addDays(iso, delta) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + delta);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function prettyDay(iso) {
  if (!iso) return "—";
  const d = new Date(`${iso}T12:00:00`);
  const today = state.context?.today;
  if (iso === today) return "Today";
  if (today && iso === addDays(today, -1)) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
/** Local wall-clock ISO, matching the naive local timestamps the app stores. */
function localIso(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
         `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

let bannerTimer = null;
function banner(message, kind = "error", ms = 4000) {
  const node = $("banner");
  node.textContent = message;
  node.className = `banner ${kind === "ok" ? "ok" : ""}`;
  clearTimeout(bannerTimer);
  if (ms) bannerTimer = setTimeout(() => node.classList.add("hidden"), ms);
}
function hideBanner() { $("banner").classList.add("hidden"); }

const subjectsByUid = () =>
  Object.fromEntries((state.snapshot?.subjects || []).map((s) => [s.uid, s]));

// ── data ────────────────────────────────────────────────────────────────

async function refresh({ quiet = false } = {}) {
  if (!quiet) $("refresh").textContent = "…";
  try {
    await flush();
    const [context, snapshot, active] = await Promise.all([
      api.context(), api.snapshot(), api.active(),
    ]);
    state.context = context;
    state.snapshot = snapshot;
    state.active = active.active ? active : null;
    if (!state.day) state.day = context.today;
    if (state.view === "sessions") await loadDay();
    hideBanner();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showSetup("That token was rejected.");
    const waiting = pendingCount();
    banner(
      err.offline
        ? `Offline${waiting ? ` — ${waiting} change(s) waiting` : ""}`
        : `Problem: ${err.message}`,
      "error",
      0,
    );
  } finally {
    $("refresh").textContent = "↻";
    render();
  }
}

async function loadDay() {
  try {
    const data = await api.day(state.day);
    state.daySessions = data.sessions || [];
  } catch {
    state.daySessions = [];
  }
}

/** Apply a write, then reload. Queued writes report themselves rather than
 *  pretending to have succeeded. */
async function act(op, params, options = {}) {
  try {
    const outcome = await send(op, params, options);
    if (outcome.queued) banner("Saved — will sync when you're back online", "ok");
    await refresh({ quiet: true });
    return outcome;
  } catch (err) {
    banner(err.message || "That did not work");
    await refresh({ quiet: true });
    return { ok: false };
  }
}

// ── rendering ───────────────────────────────────────────────────────────

function render() {
  $("view-title").textContent =
    { today: "Today", sessions: "Sessions", goals: "Goals" }[state.view];
  for (const view of ["today", "sessions", "goals"]) {
    $(`view-${view}`).classList.toggle("hidden", view !== state.view);
  }
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("on", t.dataset.view === state.view));

  if (state.view === "today") renderToday();
  if (state.view === "sessions") renderSessions();
  if (state.view === "goals") renderGoals();
}

function renderToday() {
  const card = $("active-card");
  const subjects = subjectsByUid();

  if (state.active) {
    const subject = subjects[state.active.subject_uid];
    card.classList.remove("hidden");
    $("idle-note").classList.add("hidden");
    $("active-name").textContent = subject ? subject.name : "Running";
    $("active-dot").style.background = subject ? subject.color : "var(--green)";
    startTicking();
  } else {
    card.classList.add("hidden");
    $("idle-note").classList.remove("hidden");
    stopTicking();
  }

  const list = $("subject-list");
  list.innerHTML = "";
  const active = (state.snapshot?.subjects || [])
    .filter((s) => !s.is_archived)
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

  for (const subject of active) {
    const running = state.active?.subject_uid === subject.uid;
    const card = el("div", "card tappable");
    const dot = el("div", "dot");
    dot.style.background = subject.color;
    const body = el("div", "grow");
    body.append(el("div", "name", subject.name));
    if (running) body.append(el("div", "sub", "running now"));
    card.append(dot, body);
    card.append(el("div", "muted", running ? "●" : "▶"));
    card.onclick = () => (running ? stopTimer() : startTimer(subject));
    list.append(card);
  }
}

function startTicking() {
  stopTicking();
  const started = Date.now() - (state.active?.elapsed_seconds || 0) * 1000;
  const paint = () => { $("active-elapsed").textContent = hms((Date.now() - started) / 1000); };
  paint();
  state.tick = setInterval(paint, 1000);
}
function stopTicking() {
  if (state.tick) clearInterval(state.tick);
  state.tick = null;
}

function renderSessions() {
  $("day-label").textContent = prettyDay(state.day);
  const total = state.daySessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);
  $("day-total").textContent = total ? `${hm(total)} tracked` : "nothing tracked";

  const list = $("session-list");
  list.innerHTML = "";
  if (!state.daySessions.length) {
    list.append(el("p", "muted", "No sessions on this day."));
    return;
  }
  for (const session of state.daySessions) {
    const card = el("div", "card tappable");
    const dot = el("div", "dot");
    dot.style.background = session.color || "var(--accent)";
    const body = el("div", "grow");
    body.append(el("div", "name", session.subject_name || "—"));
    body.append(el("div", "sub",
      `${clockOf(session.start_time)}–${clockOf(session.end_time)} · ${hm(session.duration_seconds)}`));
    card.append(dot, body);
    // The live session has no uid yet, exactly as on the desktop, where it is
    // shown but never editable.
    if (session.uid) {
      card.onclick = () => sessionSheet(session);
    } else {
      card.append(el("div", "muted", "running"));
    }
    list.append(card);
  }
}

function goalProgress(goalUid) {
  const all = (state.snapshot?.milestones || []).filter((m) => m.goal_uid === goalUid);
  return { done: all.filter((m) => m.is_done).length, total: all.length, items: all };
}

function renderGoals() {
  $("goals-active").classList.toggle("on", state.goalsFilter === "active");
  $("goals-done").classList.toggle("on", state.goalsFilter === "done");

  const list = $("goal-list");
  list.innerHTML = "";
  const goals = (state.snapshot?.goals || [])
    .filter((g) => (state.goalsFilter === "done" ? g.is_completed : !g.is_completed))
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

  if (!goals.length) {
    list.append(el("p", "muted",
      state.goalsFilter === "done" ? "No completed goals yet." : "No goals yet."));
    return;
  }

  for (const goal of goals) {
    const { done, total } = goalProgress(goal.uid);
    const card = el("div", "card");

    const check = el("button", "check", "✓");
    const blocked = total > 0 && done < total && !goal.is_completed;
    if (goal.is_completed) check.classList.add("done");
    if (blocked) check.classList.add("blocked");
    check.onclick = (event) => {
      event.stopPropagation();
      if (goal.is_completed) return act("uncomplete_goal", { goal_uid: goal.uid });
      if (blocked) return banner(`${total - done} milestone(s) still unchecked`);
      return act("complete_goal", { goal_uid: goal.uid });
    };

    const body = el("div", "grow");
    body.append(el("div", "name", goal.name));
    if (goal.notes) body.append(el("div", "sub", goal.notes));
    if (total) {
      body.append(el("div", "sub", `${done}/${total} milestones`));
      const bar = el("div", "bar");
      const fill = el("i");
      fill.style.width = `${(done / total) * 100}%`;
      bar.append(fill);
      body.append(bar);
    }
    body.onclick = () => goalSheet(goal);

    const star = el("button", `star ${goal.is_focused ? "on" : ""}`, goal.is_focused ? "★" : "☆");
    star.onclick = (event) => {
      event.stopPropagation();
      act("toggle_goal_focused", { goal_uid: goal.uid });
    };

    card.append(check, body, star);
    list.append(card);
  }
}

// ── sheets ──────────────────────────────────────────────────────────────

function openSheet(title, build) {
  $("sheet-title").textContent = title;
  const body = $("sheet-body");
  body.innerHTML = "";
  build(body);
  $("sheet").classList.remove("hidden");
}
function closeSheet() { $("sheet").classList.add("hidden"); }

function field(parent, label, input) {
  const wrap = el("div", "field");
  wrap.append(el("label", null, label), input);
  parent.append(wrap);
  return input;
}

function sessionSheet(session) {
  openSheet("Edit session", (body) => {
    const subjects = (state.snapshot?.subjects || []).filter((s) => !s.is_archived);
    const select = document.createElement("select");
    for (const subject of subjects) {
      const option = el("option", null, subject.name);
      option.value = subject.uid;
      if (subject.uid === session.subject_uid) option.selected = true;
      select.append(option);
    }
    field(body, "Subject", select);

    const row = el("div", "row");
    const start = Object.assign(document.createElement("input"),
      { type: "time", value: clockOf(session.start_time) });
    const end = Object.assign(document.createElement("input"),
      { type: "time", value: clockOf(session.end_time) });
    const startWrap = el("div", "field"); startWrap.append(el("label", null, "Start"), start);
    const endWrap = el("div", "field"); endWrap.append(el("label", null, "End"), end);
    row.append(startWrap, endWrap);
    body.append(row);

    const nudges = el("div", "chips");
    for (const [label, seconds] of [["−1h", -3600], ["−15m", -900], ["+15m", 900], ["+1h", 3600]]) {
      const chip = el("button", "chip", label);
      chip.onclick = async () => {
        closeSheet();
        await act("shift_session", { session_uid: session.uid, seconds });
      };
      nudges.append(chip);
    }
    body.append(el("label", null, "Nudge"), nudges);

    const save = el("button", "primary wide", "Save");
    save.onclick = async () => {
      const day = session.start_time.slice(0, 10);
      // An end time earlier than the start means it ran past midnight.
      const endDay = end.value < start.value ? addDays(day, 1) : day;
      closeSheet();
      await act("update_session", {
        session_uid: session.uid,
        subject_uid: select.value,
        start_time: `${day}T${start.value}:00`,
        end_time: `${endDay}T${end.value}:00`,
      });
    };
    body.append(save);

    const dup = el("button", "secondary wide", "Duplicate to today");
    dup.onclick = async () => {
      closeSheet();
      await act("duplicate_session", { session_uid: session.uid, to: "today" }, { uid: uuid() });
    };
    body.append(dup);

    const del = el("button", "danger wide", "Delete session");
    del.onclick = async () => {
      if (!confirm("Delete this session?")) return;
      closeSheet();
      await act("delete_session", { session_uid: session.uid });
    };
    body.append(del);
  });
}

function addSessionSheet() {
  openSheet("Add session", (body) => {
    const subjects = (state.snapshot?.subjects || []).filter((s) => !s.is_archived);
    if (!subjects.length) {
      body.append(el("p", "muted", "Create a subject on your Mac first."));
      return;
    }
    const select = document.createElement("select");
    for (const subject of subjects) {
      const option = el("option", null, subject.name);
      option.value = subject.uid;
      select.append(option);
    }
    field(body, "Subject", select);

    const now = new Date();
    const row = el("div", "row");
    const start = Object.assign(document.createElement("input"),
      { type: "time", value: `${pad(now.getHours() - 1)}:${pad(now.getMinutes())}` });
    const end = Object.assign(document.createElement("input"),
      { type: "time", value: `${pad(now.getHours())}:${pad(now.getMinutes())}` });
    const startWrap = el("div", "field"); startWrap.append(el("label", null, "Start"), start);
    const endWrap = el("div", "field"); endWrap.append(el("label", null, "End"), end);
    row.append(startWrap, endWrap);
    body.append(row);

    const save = el("button", "primary wide", "Add");
    save.onclick = async () => {
      const day = state.day;
      const endDay = end.value < start.value ? addDays(day, 1) : day;
      closeSheet();
      await act("add_session", {
        subject_uid: select.value,
        start_time: `${day}T${start.value}:00`,
        end_time: `${endDay}T${end.value}:00`,
      }, { uid: uuid() });
    };
    body.append(save);
  });
}

function goalSheet(goal) {
  openSheet(goal.name, (body) => {
    const name = field(body, "Title",
      Object.assign(document.createElement("input"), { type: "text", value: goal.name }));
    const notes = field(body, "Description",
      Object.assign(document.createElement("textarea"), { rows: 3, value: goal.notes || "" }));

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
        deadline: goal.deadline ?? null,
      });
    };
    body.append(save);

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
    const save = el("button", "primary wide", "Add goal");
    save.onclick = async () => {
      const title = name.value.trim();
      if (!title) return;
      closeSheet();
      await act("add_goal", { name: title, notes: notes.value, deadline: null }, { uid: uuid() });
    };
    body.append(save);
    setTimeout(() => name.focus(), 50);
  });
}

// ── timer actions ───────────────────────────────────────────────────────

async function startTimer(subject) {
  if (state.active) {
    if (!confirm(`Switch to ${subject.name}?`)) return;
    await act("switch_subject", { subject_uid: subject.uid });
    return;
  }
  await act("start_subject", { subject_uid: subject.uid }, { uid: uuid() });
}

async function stopTimer() {
  // Send the phone's clock, so the server applies the sub-30-second rule to the
  // time the user actually stopped rather than to whenever the request lands.
  await act("stop_active_subject", { end_time: localIso(new Date()) });
}

// ── setup screen ────────────────────────────────────────────────────────

function showSetup(message) {
  $("app").classList.add("hidden");
  $("setup").classList.remove("hidden");
  $("setup-error").textContent = message || "";
}

async function trySetup() {
  const token = $("setup-token").value.trim();
  if (!token) return;
  setToken(token);
  try {
    await api.context();
    $("setup").classList.add("hidden");
    $("app").classList.remove("hidden");
    await refresh();
  } catch (err) {
    showSetup(err.status === 401 ? "That token was not accepted." : `Could not connect: ${err.message}`);
  }
}

// ── wiring ──────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = async () => {
    state.view = tab.dataset.view;
    if (state.view === "sessions") await loadDay();
    render();
  };
});
$("refresh").onclick = () => refresh();
$("stop-btn").onclick = () => stopTimer();
$("day-prev").onclick = async () => { state.day = addDays(state.day, -1); await loadDay(); render(); };
$("day-next").onclick = async () => { state.day = addDays(state.day, 1); await loadDay(); render(); };
$("add-session").onclick = () => addSessionSheet();
$("add-goal").onclick = () => addGoalSheet();
$("goals-active").onclick = () => { state.goalsFilter = "active"; render(); };
$("goals-done").onclick = () => { state.goalsFilter = "done"; render(); };
$("sheet-close").onclick = closeSheet;
$("sheet").onclick = (event) => { if (event.target === $("sheet")) closeSheet(); };
$("setup-save").onclick = trySetup;
$("setup-token").onkeydown = (event) => { if (event.key === "Enter") trySetup(); };

// Coming back to the app is when stale data is most obvious, and the most
// likely moment to have regained signal.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && getToken()) refresh({ quiet: true });
});
window.addEventListener("online", () => refresh({ quiet: true }));

if (getToken()) {
  $("app").classList.remove("hidden");
  refresh();
} else {
  showSetup();
}
