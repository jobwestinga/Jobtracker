// JobTracker on the phone.
//
// Deliberately plain ES modules — no framework, no bundler, nothing to install
// to change a line. It renders what the server computes and never re-implements
// a rule: the logical day, the milestone gate and the sub-30-second rule all
// stay on the server, where the desktop app's own code enforces them.

import { api, send, flush, pendingCount, pendingOperations, getToken, setToken, clearToken, ApiError, uuid } from "./api.js";
import { applyChanges, projectPending } from "./offline.js";
import { renderWeight, invalidateWeight } from "./weight.js";
import { agendaNowMarker, agendaHourLabel, dayStartHour, logicalAgendaNow } from "./agenda-utils.js";
import { createFullscreenController, closeFullscreen } from "./fullscreen.js";

const $ = (id) => document.getElementById(id);
const state = {
  view: "weight",
  snapshot: null,
  baseSnapshot: null,
  baseActive: null,
  head: null,
  context: null,
  active: null,
  day: null,
  daySessions: [],
  dayStatus: "loading",
  dayCache: {},
  subjectFilter: "active",
  goalsFilter: "active",
  painted: false,
  expandedGoal: null,
  graphMode: "bars",
  graphRange: "7",
  graphGroup: "auto",
  graphStart: "",
  graphEnd: "",
  graphHourStart: 6,
  graphHourEnd: 23,
  graphAutofit: true,
  graphs: null,
  tick: null,
};
const MIRROR_KEY = "jobtracker.mirror";
const GRAPH_PREFS_KEY = "jobtracker.graphs";
try {
  const prefs = JSON.parse(localStorage.getItem(GRAPH_PREFS_KEY) || "{}");
  if (["bars", "agenda"].includes(prefs.mode)) state.graphMode = prefs.mode;
  if (["7", "30", "365", "all", "custom"].includes(prefs.range)) state.graphRange = prefs.range;
  if (["auto", "daily", "weekly", "monthly"].includes(prefs.group)) state.graphGroup = prefs.group;
  state.graphStart = prefs.start || "";
  state.graphEnd = prefs.end || "";
  if (Number.isFinite(prefs.hourStart)) state.graphHourStart = prefs.hourStart;
  if (Number.isFinite(prefs.hourEnd)) state.graphHourEnd = prefs.hourEnd;
  if (typeof prefs.autofit === "boolean") state.graphAutofit = prefs.autofit;
} catch { /* preferences are optional */ }

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
function safeColor(value, fallback = "#3B82F6") {
  return typeof value === "string" && CSS.supports("color", value) ? value : fallback;
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

function saveMirror() {
  try {
    localStorage.setItem(MIRROR_KEY, JSON.stringify({
      snapshot: state.baseSnapshot, context: state.context, head: state.head,
      dayCache: state.dayCache,
    }));
  } catch { /* the durable outbox reports storage failures; cache is optional */ }
}

function projectOutbox() {
  const projected = projectPending(state.baseSnapshot, state.baseActive, pendingOperations());
  state.snapshot = projected.snapshot;
  state.active = projected.active;
}

function restoreMirror() {
  try {
    const cached = JSON.parse(localStorage.getItem(MIRROR_KEY) || "null");
    if (cached?.snapshot) {
      state.baseSnapshot = cached.snapshot;
      state.context = cached.context;
      state.head = cached.head;
      state.day = cached.context?.today || null;
      state.dayCache = cached.dayCache || {};
      state.baseActive = cached.context?.active ? { ...cached.context.active,
        timer_origin: new Date(cached.context.active.start_time).getTime() } : null;
      projectOutbox();
      render();
      state.painted = true;
    }
  } catch (err) { banner(err.message || "Could not read cached data", "error", 0); }
}

let refreshing = null;
let mutationRevision = 0;
let forceRequested = false;
let refreshRequested = false;
function refresh(options = {}) {
  if (refreshing) {
    refreshRequested = true;
    forceRequested ||= Boolean(options.force);
    return refreshing;
  }
  refreshing = refreshOnce(options).finally(() => {
    refreshing = null;
    if (refreshRequested) {
      const force = forceRequested;
      forceRequested = false;
      refreshRequested = false;
      refresh({ quiet: true, force });
    }
  });
  return refreshing;
}

async function updateMirror(force, context) {
  if (force || !state.baseSnapshot || context.head < state.head) {
    state.baseSnapshot = await api.snapshot();
    state.head = state.baseSnapshot.head ?? context.head;
    return true;
  }
  // The feed gives only changed rows, rather than re-downloading a half-MB
  // snapshot after each checkbox or timer tap. Apply a complete pass atomically.
  let cursor = state.head;
  let snapshot = state.baseSnapshot;
  let changed = false;
  do {
    const delta = await api.pull(cursor);
    snapshot = applyChanges(snapshot, delta.changes || [], delta.settings || {});
    changed ||= Boolean(delta.changes?.length) || JSON.stringify(delta.settings || {}) !==
      JSON.stringify(state.baseSnapshot.settings || {});
    if (delta.more && delta.seq <= cursor) throw new Error("Sync did not advance; saved data is kept.");
    cursor = delta.seq;
    if (!delta.more) break;
  } while (true);
  state.baseSnapshot = snapshot;
  state.head = cursor;
  return changed;
}

async function refreshOnce({ quiet = false, force = false } = {}) {
  const revision = mutationRevision;
  if (!quiet) $("refresh").textContent = "…";
  let changedThisPass = !state.painted;
  try {
    const outgoing = await flush();
    if (outgoing.offline) throw new ApiError(outgoing.error);
    const context = await api.context();
    const followingToday = !state.day || state.day === state.context?.today;
    const changed = force || !state.baseSnapshot || context.head !== state.head ||
      context.day_start !== state.context?.day_start || context.today !== state.context?.today;
    changedThisPass = changed || state.active?.uid !== context.active?.uid ||
      state.active?.start_time !== context.active?.start_time;
    if (changed) await updateMirror(force, context);
    state.context = context;
    state.baseActive = context.active ? { ...context.active,
      timer_origin: Date.now() - (context.active.elapsed_seconds || 0) * 1000 } : null;
    if (revision === mutationRevision) projectOutbox();
    else refreshRequested = true;
    if (followingToday) state.day = context.today;
    if (changed) {
      state.dayCache = {};
      graphCache.clear();
      invalidateWeight();
    }
    if (state.active && state.day === context.today) delete state.dayCache[state.day];
    if (state.view === "sessions" && (changed || state.dayStatus !== "ready")) await loadDay();
    if (state.view === "graphs" && (changed || state.active || !state.graphs)) await loadGraphs();
    if (changed) saveMirror();
    if (!outgoing.ok) banner(`Changes waiting: ${outgoing.error}`, "error", 0);
    else hideBanner();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showSetup("That token was rejected.");
    let waiting = 0;
    try { waiting = pendingCount(); projectOutbox(); } catch (storageError) { err = storageError; }
    const showing = state.baseSnapshot ? ", showing saved data" : "";
    banner(err.offline
      ? `Offline${waiting ? ` — ${waiting} change(s) waiting` : ""}${showing}`
      : `Problem: ${err.message}`, "error", 0);
    changedThisPass = true;
  } finally {
    $("refresh").textContent = "↻";
    if (changedThisPass || !state.painted) {
      state.painted = true;
      render();
    } else paintElapsed();
  }
}

let dayRequest = 0;
async function loadDay() {
  const day = state.day;
  if (!day) return;
  const request = ++dayRequest;
  const cached = state.dayCache[day];
  if (cached) {
    state.daySessions = cached;
    state.dayStatus = "ready";
    return;
  }
  state.dayStatus = "loading";
  try {
    const data = await api.day(day);
    if (request !== dayRequest || state.day !== day) return;
    state.daySessions = data.sessions || [];
    state.dayStatus = "ready";
    state.dayCache[day] = state.daySessions;
    // Keep a small set of recent day responses for offline browsing.
    const days = Object.keys(state.dayCache);
    if (days.length > 14) delete state.dayCache[days[0]];
    saveMirror();
  } catch (err) {
    if (request !== dayRequest || state.day !== day) return;
    state.daySessions = [];
    state.dayStatus = "error";
    state.dayError = err.message;
  }
}

/**
 * Apply a write.
 *
 * `optimistic` updates local state and repaints BEFORE the request goes out, so
 * a tap feels instant instead of waiting a round trip. The server is still the
 * only authority: the refresh that follows overwrites whatever we guessed, and
 * on failure it puts the truth back on screen.
 */
async function act(op, params, options = {}) {
  mutationRevision++;
  const { optimistic, uid } = options;
  if (optimistic) {
    try { optimistic(); render(); } catch { /* never let a guess break the UI */ }
  }
  try {
    const outcome = await send(op, params, uid ? { uid } : {});
    if (outcome.queued) {
      projectOutbox();
      render();
      banner(outcome.offline ? "Saved on this phone — waiting for connection" :
        `Saved on this phone — ${outcome.error || "waiting to sync"}`, outcome.offline ? "ok" : "error", 0);
      return outcome;
    }
    if (["add_template", "update_template", "set_template_active"].includes(op)) {
      await send("generate_due_goals", {});
    }
    await refresh({ quiet: true });
    if (outcome.result?.reason) banner(outcome.result.reason, "error", 0);
    return outcome;
  } catch (err) {
    // A storage failure must roll back the optimistic display; there is no
    // durable operation to promise the user in that case.
    try { projectOutbox(); render(); } catch { /* preserve the visible last state */ }
    banner(err.message || "That did not work", "error", 0);
    return { ok: false };
  }
}

/** Local edits to the cached snapshot, used by the optimistic paths above. */
function patchGoal(uid, changes) {
  const goal = (state.snapshot?.goals || []).find((g) => g.uid === uid);
  if (goal) Object.assign(goal, changes);
}
function patchMilestone(uid, changes) {
  const milestone = (state.snapshot?.milestones || []).find((m) => m.uid === uid);
  if (milestone) Object.assign(milestone, changes);
}

// ── rendering ───────────────────────────────────────────────────────────

const VIEW_TITLES = { goals: "Goals", today: "Subjects", sessions: "Sessions", graphs: "Graphs", weight: "Weight" };

function render() {
  $("view-title").textContent = VIEW_TITLES[state.view];
  for (const view of Object.keys(VIEW_TITLES)) {
    $(`view-${view}`).classList.toggle("hidden", view !== state.view);
  }
  document.querySelectorAll(".tab").forEach((t) => {
    const selected = t.dataset.view === state.view;
    t.classList.toggle("on", selected);
    t.setAttribute("aria-current", selected ? "page" : "false");
  });

  if (state.view === "today") renderToday();
  if (state.view === "sessions") renderSessions();
  if (state.view === "goals") renderGoals();
  if (state.view === "graphs") renderGraphs();
  if (state.view === "weight") renderWeight($("weight-body"));
  if (state.view !== "today") stopTicking();
  if (state.scrollTarget !== undefined) {
    $("views").scrollTop = state.scrollTarget;
    state.scrollTarget = undefined;
  }
}

function renderToday() {
  $("subjects-active").classList.toggle("on", state.subjectFilter === "active");
  $("subjects-archived").classList.toggle("on", state.subjectFilter === "archived");
  $("add-subject").classList.toggle("hidden", state.subjectFilter === "archived");
  const card = $("active-card");
  const subjects = subjectsByUid();

  if (state.active) {
    const subject = subjects[state.active.subject_uid];
    card.classList.remove("hidden");
    $("idle-note").classList.add("hidden");
    $("active-name").textContent = subject ? subject.name : "Running";
    $("active-dot").style.backgroundColor = subject ? safeColor(subject.color) : "var(--green)";
    startTicking();
  } else {
    card.classList.add("hidden");
    $("idle-note").classList.remove("hidden");
    stopTicking();
  }

  const list = $("subject-list");
  list.innerHTML = "";
  const active = (state.snapshot?.subjects || [])
    .filter((s) => state.subjectFilter === "archived" ? s.is_archived : !s.is_archived)
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

  for (const subject of active) {
    const running = state.active?.subject_uid === subject.uid;
    const card = el("div", "card tappable");
    const dot = el("div", "dot");
    dot.style.backgroundColor = safeColor(subject.color);
    const body = el("div", "grow");
    body.append(el("div", "name", subject.name));
    if (running) body.append(el("div", "sub", "running now"));
    card.append(dot, body);
    const more = el("button", "icon-btn", "⋯");
    more.setAttribute("aria-label", `Edit ${subject.name}`);
    more.onclick = (event) => { event.stopPropagation(); subjectSheet(subject); };
    card.append(more);
    if (!subject.is_archived) {
      const toggle = el("button", "subject-start", running ? "●" : "▶");
      toggle.setAttribute("aria-label", `${running ? "Stop" : "Start"} ${subject.name}`);
      toggle.onclick = (event) => { event.stopPropagation(); running ? stopTimer() : startTimer(subject); };
      card.append(toggle);
      card.onclick = () => (running ? stopTimer() : startTimer(subject));
    } else card.onclick = () => subjectSheet(subject);
    list.append(card);
  }
  if (!active.length) list.append(el("p", "muted", state.subjectFilter === "archived" ?
    "No archived subjects." : "Add a subject to start tracking."));
}

function paintElapsed() {
  if (state.view === "today" && state.active) startTicking();
}

function startTicking() {
  stopTicking();
  if (document.hidden) return;
  const started = state.active?.timer_origin ?? Date.now() - (state.active?.elapsed_seconds || 0) * 1000;
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
  if (document.activeElement !== $("day-date")) $("day-date").value = state.day || "";
  const total = state.daySessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);
  $("day-total").textContent = total ? `${hm(total)} tracked` : "nothing tracked";

  const list = $("session-list");
  list.innerHTML = "";
  if (state.dayStatus !== "ready") {
    $("day-total").textContent = "";
    list.append(el("p", "muted", state.dayStatus === "loading" ? "Loading…" :
      `Could not load this day: ${state.dayError || "no connection"}`));
    return;
  }
  if (!state.daySessions.length) {
    list.append(el("p", "muted", "No sessions on this day."));
    return;
  }
  for (const session of state.daySessions) {
    const card = el("div", "card tappable");
    const dot = el("div", "dot");
    dot.style.backgroundColor = safeColor(session.color);
    const body = el("div", "grow");
    body.append(el("div", "name", session.subject_name || "—"));
    body.append(el("div", "sub",
      `${clockOf(session.start_time)}–${clockOf(session.end_time)} · ${hm(session.duration_seconds)}`));
    card.append(dot, body);
    // The live session has no uid yet, exactly as on the desktop, where it is
    // shown but never editable.
    if (session.uid && session.end_time) {
      card.onclick = () => sessionSheet(session);
      const edit = el("button", "icon-btn", "⋯");
      edit.setAttribute("aria-label", `Edit session for ${session.subject_name || "subject"}`);
      edit.onclick = (event) => { event.stopPropagation(); sessionSheet(session); };
      card.append(edit);
    } else {
      card.append(el("div", "muted", "running"));
    }
    list.append(card);
  }
}

// Milestones grouped by goal, built once per snapshot rather than re-filtering
// the whole list for every goal. That was 90 goals x 458 milestones on every
// render, and a render happens after every tap.
let milestoneIndex = { source: null, map: new Map() };

function milestonesByGoal() {
  const milestones = state.snapshot?.milestones || [];
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

const milestoneDrafts = new Map();
const milestoneSaves = new Set();

function renderGoals() {
  const focused = document.activeElement;
  const focusGoal = focused?.dataset?.milestoneGoal;
  const selection = focusGoal ? [focused.selectionStart, focused.selectionEnd] : null;
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

/**
 * One goal, with its milestones tickable in place.
 *
 * Opening an editor to check something off was the wrong shape: reading the list
 * and ticking things are the everyday actions, editing is rare. So the card
 * expands inline and every checkbox is one tap, while editing hides behind "⋯".
 */
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
      if (state.view === "goals") renderGoals();
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


// ── graphs ──────────────────────────────────────────────────────────────
//
// The server computes every total (same logical-day attribution the desktop
// uses), so these numbers cannot drift from the Mac's. This code only draws.

/** The bucket size a range implies, matching the desktop's grouping_for_preset. */
function automaticGrouping(range) {
  if (range >= 365) return "monthly";
  if (range >= 30) return "weekly";
  return "daily";
}

const graphCache = new Map();
let graphRequest = 0;
let graphPaintKey = null;
let graphFullscreen = null;
let agendaLayout = null;
let graphResizeFrame = null;
const agendaScroll = new Map();
function saveGraphPreferences() {
  try {
    localStorage.setItem(GRAPH_PREFS_KEY, JSON.stringify({
      mode: state.graphMode, range: state.graphRange, group: state.graphGroup,
      start: state.graphStart, end: state.graphEnd,
      hourStart: state.graphHourStart, hourEnd: state.graphHourEnd, autofit: state.graphAutofit,
    }));
  } catch { /* optional */ }
}

async function loadGraphs() {
  const request = ++graphRequest;
  const range = state.graphRange === "all" ? 3660 : state.graphRange === "custom" ? 7 : Number(state.graphRange);
  const options = {};
  if (state.graphRange === "all" && state.graphMode === "bars") options.all_time = true;
  if (state.graphRange === "custom") {
    if (!state.graphStart || !state.graphEnd || state.graphStart > state.graphEnd) {
      state.graphs = { kind: "error", message: "Choose a start and end date in order." };
      return;
    }
    options.start_date = state.graphStart;
    options.end_date = state.graphEnd;
  }
  const kind = state.graphMode;
  const grouping = state.graphGroup === "auto" ? automaticGrouping(range) : state.graphGroup;
  const key = JSON.stringify([kind, range, grouping, options]);
  if (graphCache.has(key) && !state.active) {
    state.graphs = graphCache.get(key);
    return;
  }
  try {
    const data = kind === "agenda" ? await api.agenda(Math.min(range, 60), options) :
      await api.breakdown(grouping, range, options);
    if (request !== graphRequest) return;
    state.graphs = { kind, data };
    graphCache.set(key, state.graphs);
  } catch (err) {
    if (request === graphRequest) state.graphs = { kind: "error", message: err.message };
  }
}

function renderGraphs() {
  $("graph-custom").classList.toggle("hidden", state.graphRange !== "custom");
  if (document.activeElement !== $("graph-start")) $("graph-start").value = state.graphStart;
  if (document.activeElement !== $("graph-end")) $("graph-end").value = state.graphEnd;
  $("graph-options").classList.toggle("hidden", state.graphMode !== "agenda");
  document.querySelectorAll("#view-graphs [data-mode]").forEach((b) =>
    b.classList.toggle("on", b.dataset.mode === state.graphMode));
  document.querySelectorAll("#view-graphs [data-range]").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === state.graphRange));
  document.querySelectorAll("#view-graphs [data-group]").forEach((b) =>
    b.classList.toggle("on", b.dataset.group === state.graphGroup));
  // Bucket size means nothing to the agenda, which is always one column per day.
  document.querySelector("#view-graphs .seg-group")
    .classList.toggle("hidden", state.graphMode !== "bars");

  const host = $("graph-body");
  const paintKey = JSON.stringify([state.graphs, state.graphRange, state.graphHourStart, state.graphHourEnd, state.graphAutofit]);
  if (paintKey === graphPaintKey) {
    updateAgendaNow();
    queueGraphResize();
    return;
  }
  graphPaintKey = paintKey;
  agendaLayout = null;
  host.innerHTML = "";
  $("graph-legend").innerHTML = "";

  if (!state.graphs) {
    $("graph-total").textContent = "";
    host.append(el("div", "spinner", "Loading…"));
    return;
  }
  if (state.graphs.kind === "error") {
    $("graph-total").textContent = "";
    host.append(el("p", "muted", `Could not load graphs: ${state.graphs.message}`));
    return;
  }
  if (state.graphs.kind === "agenda") return drawAgenda(host, state.graphs.data);
  return drawBars(host, state.graphs.data);
}

function drawBars(host, data) {
  const buckets = data.buckets || [];
  const total = buckets.reduce((sum, b) => sum + b.total_seconds, 0);
  $("graph-total").textContent =
    `${hm(total)} across ${buckets.length} ${data.grouping === "daily" ? "days" : data.grouping === "weekly" ? "weeks" : "months"}`;

  if (!buckets.length) {
    host.append(el("p", "muted", "Nothing tracked in this range."));
    return;
  }

  const peak = Math.max(...buckets.map((b) => b.total_seconds), 1);
  const chart = el("div", "bars");
  for (const bucket of buckets) {
    const column = el("div", "bar-col");
    const stack = el("div", "bar-stack");
    // Sum per SUBJECT first. The API returns one segment per session, and a
    // month can hold fifty of them; with a minimum height per block, every long
    // bucket hit the ceiling and all the bars came out the same height. This is
    // also what "stacked by subject" is supposed to mean.
    const perSubject = new Map();
    for (const segment of bucket.segments) {
      const key = segment.subject_uid || segment.subject_name;
      const entry = perSubject.get(key)
        || { seconds: 0, color: segment.color, name: segment.subject_name };
      entry.seconds += segment.seconds;
      perSubject.set(key, entry);
    }
    const segments = [...perSubject.values()].sort((a, b) => b.seconds - a.seconds);
    for (const segment of segments) {
      const piece = el("div", "bar-seg");
      piece.style.height = `${(segment.seconds / peak) * 100}%`;
      piece.style.backgroundColor = safeColor(segment.color);
      piece.title = `${segment.name}: ${hm(segment.seconds)}`;
      stack.append(piece);
    }
    const value = el("div", "bar-val", bucket.total_seconds ? hm(bucket.total_seconds) : "");
    const label = el("div", "bar-lbl", shortLabel(bucket.date, data.grouping));
    column.append(value, stack, label);
    // Tapping a day opens exactly that day, the way the desktop's charts do.
    if (data.grouping === "daily") {
      column.onclick = () => navigateDay(bucket.date);
    }
    chart.append(column);
  }
  host.append(chart);
  queueGraphResize();

  // Legend: which colour is which subject, biggest first.
  const totals = new Map();
  for (const bucket of buckets) {
    for (const segment of bucket.segments) {
      const entry = totals.get(segment.subject_name) || { seconds: 0, color: segment.color };
      entry.seconds += segment.seconds;
      totals.set(segment.subject_name, entry);
    }
  }
  const legend = $("graph-legend");
  [...totals.entries()]
    .sort((a, b) => b[1].seconds - a[1].seconds)
    .slice(0, 8)
    .forEach(([name, entry]) => {
      const chip = el("div", "legend-item");
      const dot = el("span", "dot");
      dot.style.backgroundColor = safeColor(entry.color);
      chip.append(dot, el("span", null, `${name} · ${hm(entry.seconds)}`));
      legend.append(chip);
    });
}


function drawAgenda(host, data) {
  const days = data.days || [];
  const sessions = (data.sessions || []).filter((session) => Number.isFinite(session.start_h) && Number.isFinite(session.end_h));
  const total = sessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);
  $("graph-total").textContent = `${hm(total)} across ${days.length} days${state.graphRange === "all" ? " (latest 60 days)" : ""}`;

  if (!days.length) {
    host.append(el("p", "muted", "No days in this range."));
    return;
  }

  // Fit the window to the work actually done, but never narrower than a normal
  // day, so a single early session does not stretch one block over the screen.
  const lo = Math.max(0, Math.min(26, state.graphAutofit && sessions.length ? Math.min(state.graphHourStart, Math.floor(Math.min(...sessions.map((s) => s.start_h)))) : state.graphHourStart));
  const hi = Math.max(lo + 1, Math.min(27, state.graphAutofit && sessions.length ? Math.max(state.graphHourEnd, Math.ceil(Math.max(...sessions.map((s) => s.end_h)))) : state.graphHourEnd));
  const span = Math.max(1, hi - lo);
  const height = 300;
  const percentFor = (hour) => ((hour - lo) / span) * 100;

  const wrap = el("div", "agenda-wrap");

  const gutter = el("div", "agenda-hours");
  gutter.style.height = `${height}px`;
  for (let hour = Math.ceil(lo); hour <= hi; hour += span > 12 ? 3 : 2) {
    const mark = el("span", null, agendaHourLabel(hour));
    mark.style.top = `${percentFor(hour)}%`;
    gutter.append(mark);
  }
  wrap.append(gutter);

  const scroll = el("div", "agenda-scroll");
  const grid = el("div", "agenda-grid");
  const tracks = new Map();
  const byDay = new Map(days.map((d) => [d, []]));
  for (const session of sessions) {
    if (byDay.has(session.day)) byDay.get(session.day).push(session);
  }

  for (const day of days) {
    const column = el("div", "agenda-col");
    column.dataset.day = day;
    const track = el("div", "agenda-track");
    track.dataset.day = day;
    track.style.height = `${height}px`;
    tracks.set(day, track);

    for (const session of byDay.get(day)) {
      if (session.end_h <= lo || session.start_h >= hi) continue;
      const top = percentFor(Math.max(lo, session.start_h));
      const blockHeight = Math.max(0, percentFor(Math.min(hi, session.end_h)) - top);
      const block = el("div", "agenda-block");
      block.style.top = `${top}%`;
      block.style.height = `${blockHeight}%`;
      block.style.borderLeftColor = safeColor(session.color);
      // Neutral base plus a tint of the subject colour, as on the desktop.
      block.style.background = `color-mix(in srgb, ${safeColor(session.color)} 35%, var(--panel-2))`;
      if (blockHeight / 100 * height > 16) block.textContent = session.subject_name;
      block.title = `${session.subject_name} · ${hm(session.duration_seconds)}`;
      block.onclick = (event) => {
        event.stopPropagation();
        const persisted = (state.snapshot?.sessions || []).find((row) => row.uid === session.uid);
        if (persisted?.end_time) sessionSheet({ ...persisted, subject_name: session.subject_name });
        else navigateDay(day);
      };
      track.append(block);
    }

    // Tapping a day opens it in Sessions, where it can actually be edited.
    track.onclick = () => navigateDay(day);

    const label = el("div", "agenda-daylbl", prettyDayShort(day));
    column.append(track, label);
    grid.append(column);
  }

  scroll.append(grid);
  wrap.append(scroll);
  host.append(wrap);
  const scrollKey = JSON.stringify([state.graphRange, state.graphStart, state.graphEnd]);
  scroll.addEventListener("scroll", () => {
    // A wide rotated viewport may temporarily fit every day. Preserve the
    // user's intended position for the narrow viewport instead of caching0.
    if (agendaLayout?.scroll === scroll && !agendaLayout.restoring && scroll.scrollWidth > scroll.clientWidth) {
      agendaScroll.set(scrollKey, scroll.scrollLeft);
    }
  }, { passive: true });
  agendaLayout = { days, lo, hi, wrap, gutter, scroll, tracks, marker: null,
    scrollKey, restoring: false, boundary: dayStartHour(data, state.context?.day_start) };
  updateAgendaNow();
  requestAnimationFrame(() => {
    // A refresh keeps exactly the days the user was looking at. A new range
    // opens at the most recent end, matching the original view.
    if (!scroll.isConnected) return;
    scroll.scrollLeft = agendaScroll.get(scrollKey) ?? scroll.scrollWidth;
    if (!agendaScroll.has(scrollKey)) {
      // Capture the initial latest-day position before resize suppresses scroll
      // events. An initially wide viewport should also reopen at the latest end.
      agendaScroll.set(scrollKey, scroll.scrollWidth > scroll.clientWidth ? scroll.scrollLeft : scroll.scrollWidth);
    }
    queueGraphResize();
  });
}

function updateAgendaNow() {
  if (!agendaLayout || state.view !== "graphs" || state.graphMode !== "agenda") return;
  const layout = agendaLayout;
  const now = agendaNowMarker(layout.days, layout.lo, layout.hi, { boundary: layout.boundary });
  if (!now) { layout.marker?.remove(); layout.marker = null; return; }
  if (!layout.marker) {
    layout.marker = el("div", "agenda-now");
    layout.marker.append(el("span", "agenda-now-label"));
    layout.marker.setAttribute("aria-hidden", "true");
  }
  const track = layout.tracks.get(now.day);
  if (layout.marker.parentElement !== track) track.append(layout.marker);
  layout.marker.dataset.day = now.day;
  layout.marker.style.top = `${now.fraction * 100}%`;
  layout.marker.firstElementChild.textContent = now.label;
  layout.marker.title = `Now ${now.label}`;
}

function queueGraphResize() {
  if (graphResizeFrame !== null) return;
  if (agendaLayout) agendaLayout.restoring = true;
  graphResizeFrame = requestAnimationFrame(() => {
    graphResizeFrame = null;
    if (state.view !== "graphs") return;
    const fullscreen = graphFullscreen?.isOpen;
    const chart = agendaLayout?.wrap || $("graph-body").querySelector(".bars");
    if (!chart) return;
    const panel = $("view-graphs");
    const available = panel.clientHeight - (chart.getBoundingClientRect().top - panel.getBoundingClientRect().top) - 44;
    const height = fullscreen ? Math.max(200, available) : agendaLayout ? 300 : 260;
    if (agendaLayout) {
      const layout = agendaLayout;
      for (const node of [layout.gutter, ...layout.tracks.values()]) node.style.height = `${height}px`;
      if (agendaScroll.has(layout.scrollKey)) layout.scroll.scrollLeft = agendaScroll.get(layout.scrollKey);
      requestAnimationFrame(() => { layout.restoring = false; });
      updateAgendaNow();
    } else chart.style.height = `${height}px`;
  });
}

function prettyDayShort(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return `${d.toLocaleDateString(undefined, { weekday: "short" }).slice(0, 2)} ${d.getDate()}`;
}

function shortLabel(iso, grouping) {
  const d = new Date(`${iso}T12:00:00`);
  if (grouping === "monthly") return d.toLocaleDateString(undefined, { month: "short" });
  if (grouping === "weekly") return `${d.getDate()}/${d.getMonth() + 1}`;
  return d.toLocaleDateString(undefined, { weekday: "narrow" });
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

let fieldId = 0;
function field(parent, label, input) {
  const wrap = el("div", "field");
  input.id ||= `field-${++fieldId}`;
  const caption = el("label", null, label);
  caption.htmlFor = input.id;
  wrap.append(caption, input);
  parent.append(wrap);
  return input;
}

function validSessionInputs(start, end, subject) {
  for (const input of [start, end, subject]) {
    if (!input.checkValidity()) { input.reportValidity(); return false; }
  }
  if (!subject.value) { banner("Choose a subject first."); return false; }
  if (!start.value || !end.value || end.value < start.value) {
    banner("End must be at or after start. Include the next date for overnight sessions.");
    return false;
  }
  return true;
}

function reorderButtons(body, rows, uid, operation, params = {}) {
  const ordered = [...rows].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
  const index = ordered.findIndex((row) => row.uid === uid);
  const controls = el("div", "row");
  for (const [text, offset] of [["Move up", -1], ["Move down", 1]]) {
    const button = el("button", "ghost", text);
    button.disabled = index + offset < 0 || index + offset >= ordered.length;
    button.onclick = () => {
      const next = [...ordered];
      [next[index], next[index + offset]] = [next[index + offset], next[index]];
      closeSheet();
      const key = operation === "set_subject_order" ? "subject_uids" : "goal_uids";
      act(operation, { ...params, [key]: next.map((row) => row.uid) });
    };
    controls.append(button);
  }
  body.append(el("label", null, "List order"), controls);
}

function subjectSheet(subject) {
  openSheet(subject.name, (body) => {
    const name = field(body, "Name", Object.assign(document.createElement("input"),
      { type: "text", value: subject.name, required: true }));
    const color = field(body, "Colour", Object.assign(document.createElement("input"),
      { type: "color", value: /^#[0-9a-f]{6}$/i.test(subject.color) ? subject.color : "#3B82F6" }));
    const notes = field(body, "Description", Object.assign(document.createElement("textarea"),
      { rows: 3, value: subject.notes || "" }));
    const save = el("button", "primary wide", "Save changes");
    save.onclick = () => {
      if (!name.value.trim()) return name.reportValidity();
      if ((state.snapshot?.subjects || []).some((row) => row.uid !== subject.uid && row.name === name.value.trim())) {
        return banner("That subject name already exists, including archived subjects.");
      }
      closeSheet();
      act("update_subject", { subject_uid: subject.uid, name: name.value.trim(), color: color.value, notes: notes.value });
    };
    body.append(save);
    reorderButtons(body, (state.snapshot?.subjects || []).filter((row) => Boolean(row.is_archived) === Boolean(subject.is_archived)),
      subject.uid, "set_subject_order", { archived: Boolean(subject.is_archived) });
    const history = el("button", "secondary wide", "Session history");
    history.onclick = () => subjectHistorySheet(subject);
    body.append(history);
    const archive = el("button", "secondary wide", subject.is_archived ? "Restore subject" : "Archive subject");
    archive.onclick = () => {
      if (!subject.is_archived && state.active?.subject_uid === subject.uid) {
        return banner("Stop this subject before archiving it.");
      }
      closeSheet();
      act(subject.is_archived ? "unarchive_subject" : "archive_subject", { subject_uid: subject.uid });
    };
    body.append(archive);
  });
}

function subjectHistorySheet(subject) {
  openSheet(`${subject.name} sessions`, (body) => {
    const sessions = (state.snapshot?.sessions || []).filter((row) => row.subject_uid === subject.uid)
      .sort((a, b) => b.start_time.localeCompare(a.start_time));
    if (!sessions.length) body.append(el("p", "muted", "No sessions yet."));
    for (const session of sessions) {
      const row = el("button", "ghost wide");
      row.append(el("div", "name", session.start_time.slice(0, 10)),
        el("div", "sub", `${clockOf(session.start_time)}–${clockOf(session.end_time)}${session.end_time ? "" : " · running"}`));
      row.disabled = !session.end_time;
      row.onclick = () => sessionSheet(session);
      body.append(row);
    }
  });
}

function graphOptionsSheet() {
  openSheet("Agenda hours", (body) => {
    const start = field(body, "Start hour (0–26)", Object.assign(document.createElement("input"),
      { type: "number", min: "0", max: "26", required: true, value: state.graphHourStart }));
    const end = field(body, "End hour (1–27; 25 is 01:00 next day)", Object.assign(document.createElement("input"),
      { type: "number", min: "1", max: "27", required: true, value: state.graphHourEnd }));
    const autofit = Object.assign(document.createElement("input"), { type: "checkbox", checked: state.graphAutofit });
    field(body, "Expand hours to include sessions", autofit);
    const save = el("button", "primary wide", "Save");
    save.onclick = () => {
      if (!start.checkValidity() || !end.checkValidity() || Number(end.value) <= Number(start.value)) {
        return banner("Choose an end hour after the start, within 0–27.");
      }
      state.graphHourStart = Number(start.value);
      state.graphHourEnd = Number(end.value);
      state.graphAutofit = autofit.checked;
      saveGraphPreferences();
      closeSheet();
      renderGraphs();
    };
    body.append(save);
  });
}

function sessionSheet(session) {
  if (!session.end_time) return banner("Stop this running session before editing it.");
  openSheet("Edit session", (body) => {
    const subjects = (state.snapshot?.subjects || []).filter((s) => !s.is_archived || s.uid === session.subject_uid);
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
      { type: "datetime-local", step: "1", required: true, value: session.start_time.slice(0, 19) });
    const end = Object.assign(document.createElement("input"),
      { type: "datetime-local", step: "1", required: true, value: session.end_time.slice(0, 19) });
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
      if (!validSessionInputs(start, end, select)) return;
      closeSheet();
      await act("update_session", {
        session_uid: session.uid,
        subject_uid: select.value,
        start_time: start.value.length === 16 ? `${start.value}:00` : start.value,
        end_time: end.value.length === 16 ? `${end.value}:00` : end.value,
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
      body.append(el("p", "muted", "Add a subject in the Subjects tab first."));
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
    const from = new Date(now.getTime() - 3600000);
    const today = state.day === state.context?.today;
    const row = el("div", "row");
    const start = Object.assign(document.createElement("input"),
      { type: "datetime-local", required: true, value: today ? localIso(from).slice(0, 16) : `${state.day}T09:00` });
    const end = Object.assign(document.createElement("input"),
      { type: "datetime-local", required: true, value: today ? localIso(now).slice(0, 16) : `${state.day}T10:00` });
    const startWrap = el("div", "field"); startWrap.append(el("label", null, "Start"), start);
    const endWrap = el("div", "field"); endWrap.append(el("label", null, "End"), end);
    row.append(startWrap, endWrap);
    body.append(row);

    // The desktop has "Quick Add (ending now)"; the same idea earns its place
    // even more on a phone, where typing two times is the slow part.
    const quick = el("div", "chips");
    for (const [label, minutes] of [["15m", 15], ["30m", 30], ["45m", 45],
                                    ["1h", 60], ["1h30", 90], ["2h", 120]]) {
      const chip = el("button", "chip", label);
      chip.onclick = async () => {
        const now = new Date();
        const from = new Date(now.getTime() - minutes * 60000);
        closeSheet();
        await act("add_session", {
          subject_uid: select.value,
          start_time: localIso(from),
          end_time: localIso(now),
        }, { uid: uuid() });
      };
      quick.append(chip);
    }
    if (today) body.append(el("label", null, "Quick add, ending now"), quick);

    const save = el("button", "primary wide", "Add with these times");
    save.onclick = async () => {
      if (!validSessionInputs(start, end, select)) return;
      closeSheet();
      await act("add_session", {
        subject_uid: select.value,
        start_time: `${start.value}:00`,
        end_time: `${end.value}:00`,
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
    reorderButtons(body, (state.snapshot?.goals || []).filter((row) => Boolean(row.is_completed) === Boolean(goal.is_completed)),
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

function templatesSheet() {
  openSheet("Recurring goals", (body) => {
    const templates = state.snapshot?.templates || [];
    if (!templates.length) body.append(el("p", "muted", "Create a daily, weekly, or monthly goal."));
    for (const template of templates) {
      const row = el("button", "ghost wide");
      row.append(el("div", "name", template.title), el("div", "sub",
        `${template.recurrence}${template.is_active ? "" : " · paused"}`));
      row.onclick = () => templateSheet(template);
      body.append(row);
    }
    const add = el("button", "primary wide", "+ Add recurring goal");
    add.onclick = () => templateSheet();
    body.append(add);
  });
}

function templateSheet(template = null) {
  openSheet(template ? "Edit recurring goal" : "New recurring goal", (body) => {
    const title = field(body, "Title ({date} inserts the date)", Object.assign(document.createElement("input"),
      { type: "text", required: true, value: template?.title || "" }));
    const notes = field(body, "Description", Object.assign(document.createElement("textarea"),
      { rows: 3, value: template?.notes || "" }));
    const recurrence = document.createElement("select");
    for (const key of ["daily", "weekly", "monthly"]) {
      const option = el("option", null, key[0].toUpperCase() + key.slice(1));
      option.value = key;
      recurrence.append(option);
    }
    recurrence.value = template?.recurrence || "daily";
    field(body, "Repeats", recurrence);
    const recurrenceDay = field(body, "Schedule day", Object.assign(document.createElement("input"),
      { type: "number", min: "1", max: "31", value: template?.recurrence_day || 1 }));
    const hint = el("p", "muted");
    const schedule = () => {
      recurrenceDay.parentElement.classList.toggle("hidden", recurrence.value === "daily");
      recurrenceDay.max = recurrence.value === "weekly" ? "7" : "31";
      hint.textContent = recurrence.value === "weekly" ? "1 is Monday, 7 is Sunday." :
        recurrence.value === "monthly" ? "Short months use their last day." : "Generated once per logical day.";
    };
    recurrence.onchange = schedule; schedule(); body.append(hint);
    let initial = [];
    try { initial = JSON.parse(template?.milestones_json || "[]"); } catch { /* preserve readable parts */ }
    const milestones = field(body, "Milestones (one per line)", Object.assign(document.createElement("textarea"),
      { rows: 4, value: initial.map((item) => typeof item === "string" ? item : item.title || "").join("\n") }));
    const save = el("button", "primary wide", template ? "Save recurring goal" : "Add recurring goal");
    save.onclick = () => {
      if (!title.value.trim()) return title.reportValidity();
      if (recurrence.value !== "daily" && !recurrenceDay.checkValidity()) return recurrenceDay.reportValidity();
      closeSheet();
      act(template ? "update_template" : "add_template", {
        ...(template ? { template_uid: template.uid } : {}), title: title.value.trim(), notes: notes.value,
        recurrence: recurrence.value, recurrence_day: recurrence.value === "daily" ? null : Number(recurrenceDay.value),
        milestone_titles: milestones.value.split("\n").map((line) => line.trim()).filter(Boolean)
          .map((line) => ({ title: line, note: initial.find((item) => typeof item === "object" && item.title === line)?.note || "" })),
      }, template ? {} : { uid: uuid() });
    };
    body.append(save);
    if (template) {
      const toggle = el("button", "secondary wide", template.is_active ? "Pause recurrence" : "Resume recurrence");
      toggle.onclick = () => {
        closeSheet(); act("set_template_active", { template_uid: template.uid, active: !template.is_active });
      };
      const remove = el("button", "danger wide", "Delete recurring goal");
      remove.onclick = () => {
        if (!confirm("Delete this recurrence? Existing goals stay in your list.")) return;
        closeSheet(); act("delete_template", { template_uid: template.uid });
      };
      body.append(toggle, remove);
    }
  });
}



function addSubjectSheet() {
  openSheet("New subject", (body) => {
    const name = field(body, "Name",
      Object.assign(document.createElement("input"),
        { type: "text", placeholder: "e.g. Thesis" }));

    body.append(el("label", null, "Colour"));
    // The desktop suggests colours that stay distinct from the existing ones;
    // on the phone a fixed palette is enough, with the already-used ones marked.
    const used = new Set((state.snapshot?.subjects || []).filter((s) => !s.is_archived).map((s) => s.color));
    const chips = el("div", "chips");
    let chosen = null;
    const palette = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6",
                     "#EC4899", "#14B8A6", "#F97316", "#6366F1", "#84CC16"];
    for (const colour of palette) {
      const chip = el("button", "chip swatch");
      chip.style.background = colour;
      if (used.has(colour)) chip.classList.add("used");
      chip.onclick = () => {
        chosen = colour;
        chips.querySelectorAll(".swatch").forEach((c) => c.classList.remove("picked"));
        chip.classList.add("picked");
      };
      chips.append(chip);
    }
    body.append(chips);

    const save = el("button", "primary wide", "Add subject");
    save.onclick = async () => {
      const title = name.value.trim();
      if (!title) return;
      if ((state.snapshot?.subjects || []).some((row) => row.name === title)) {
        return banner("That subject name already exists, including archived subjects.");
      }
      const colour = chosen || palette.find((c) => !used.has(c)) || palette[0];
      closeSheet();
      await act("add_subject", { name: title, color: colour, notes: "" }, { uid: uuid() });
    };
    body.append(save);
    setTimeout(() => name.focus(), 50);
  });
}

// ── settings ────────────────────────────────────────────────────────────

function settingsSheet() {
  openSheet("Settings", (body) => {
    // Day start is a SHARED setting: changing it here changes it on the Mac too,
    // because it describes the data rather than this device. Theme and graph
    // preferences stay per-machine and deliberately are not offered here.
    const dayRow = el("div", "set-row");
    dayRow.append(el("div", "k", "Day starts at"));
    const daySelect = document.createElement("select");
    for (let hour = 0; hour < 24; hour += 1) {
      const option = el("option", null, `${pad(hour)}:00`);
      option.value = `${pad(hour)}:00`;
      if (option.value === state.context?.day_start) option.selected = true;
      daySelect.append(option);
    }
    daySelect.onchange = async () => {
      closeSheet();
      const result = await act("set_setting", { key: "day_start_time", value: daySelect.value });
      if (result.ok) banner("Day start updated — shared with your Mac", "ok");
    };
    dayRow.append(daySelect);
    body.append(dayRow);
    body.append(el("p", "set-note",
      "Late-night work counts towards the day it started. Shared with your Mac."));

    const info = [
      ["Today (logical)", state.context?.today || "—"],
      ["Server time", (state.context?.server_time || "—").replace("T", " ")],
      ["Subjects", String((state.snapshot?.subjects || []).filter((x) => !x.is_archived).length)],
      ["Sessions stored", String((state.snapshot?.sessions || []).length)],
      ["Goals", String((state.snapshot?.goals || []).length)],
      ["Waiting to send", String(pendingCount())],
    ];
    for (const [key, value] of info) {
      const row = el("div", "set-row");
      row.append(el("div", "k", key), el("div", "v", value));
      body.append(row);
    }

    const retry = el("button", "secondary wide", "Send anything waiting");
    retry.onclick = async () => {
      const outcome = await flush();
      banner(outcome.ok
        ? (outcome.sent ? `Sent ${outcome.sent} change(s)` : "Nothing waiting")
        : `Changes kept: ${outcome.error || "waiting for connection"}`, outcome.ok ? "ok" : "error");
      closeSheet();
      refresh({ quiet: true });
    };
    body.append(retry);

    const reload = el("button", "secondary wide", "Reload app");
    reload.onclick = () => location.reload();
    body.append(reload);

    const out = el("button", "danger wide", "Disconnect this phone");
    out.onclick = () => {
      if (pendingCount()) return banner("Send the waiting changes before disconnecting this phone.", "error", 0);
      if (!confirm("Remove the token from this phone? Your data stays on the server.")) return;
      localStorage.removeItem(MIRROR_KEY);
      localStorage.removeItem("jobtracker.weight-cache");
      clearToken();
      location.reload();
    };
    body.append(out);
  });
}

// ── timer actions ───────────────────────────────────────────────────────

async function startTimer(subject) {
  let start = localIso(new Date());
  const uid = uuid();
  if (state.active) {
    if (!confirm(`Switch to ${subject.name}?`)) return;
    start = localIso(new Date());
    const sourceUid = state.active.session_uid || state.active.uid;
    await act("switch_subject", { subject_uid: subject.uid, start_time: start,
      end_time: start, source_session_uid: sourceUid }, {
      uid,
      optimistic: () => { state.active = { uid, session_uid: uid, subject_uid: subject.uid,
        start_time: start, elapsed_seconds: 0, timer_origin: Date.now() }; },
    });
    return;
  }
  // Show the timer running the moment it is tapped. Waiting for the round trip
  // made a start feel like it had not registered.
  await act("start_subject", { subject_uid: subject.uid, start_time: start }, {
    uid,
    optimistic: () => { state.active = { uid, session_uid: uid, subject_uid: subject.uid,
      start_time: start, elapsed_seconds: 0, timer_origin: Date.now() }; },
  });
}

async function stopTimer() {
  if (!state.active) return;
  // Send the phone's clock, so the server applies the sub-30-second rule to the
  // time the user actually stopped rather than to whenever the request lands.
  await act("stop_active_subject", { session_uid: state.active.session_uid || state.active.uid,
    end_time: localIso(new Date()) }, {
    optimistic: () => { state.active = null; },
  });
}

// ── setup screen ────────────────────────────────────────────────────────

function showSetup(message) {
  closeFullscreen();
  stopTicking();
  $("app").classList.add("hidden");
  $("setup").classList.remove("hidden");
  $("setup-error").textContent = message || "";
}

async function trySetup() {
  const token = $("setup-token").value.trim();
  if (!token) return;
  try {
    const previous = getToken();
    setToken(token);
    await api.context();
    if (previous !== token) {
      closeFullscreen();
      state.view = "weight";
      milestoneDrafts.clear();
      localStorage.removeItem(MIRROR_KEY);
      localStorage.removeItem("jobtracker.weight-cache");
      state.baseSnapshot = null;
      state.baseActive = null;
      state.snapshot = null;
      state.context = null;
      state.day = null;
      state.expandedGoal = null;
      state.head = null;
      state.dayCache = {};
      state.daySessions = [];
      state.graphs = null;
      graphCache.clear();
      agendaScroll.clear();
      viewScroll.clear();
      invalidateWeight();
    }
    $("setup-token").value = "";
    $("setup").classList.add("hidden");
    $("app").classList.remove("hidden");
    render();
    await refresh();
  } catch (err) {
    showSetup(err.status === 401 ? "That token was not accepted." : `Could not connect: ${err.message}`);
  }
}

// ── wiring ──────────────────────────────────────────────────────────────

const viewScroll = new Map();
function switchView(view) {
  if (state.view !== view) {
    closeFullscreen();
    viewScroll.set(state.view, $("views").scrollTop);
    state.view = view;
    state.scrollTarget = viewScroll.get(view) || 0;
  }
}

async function navigateDay(day) {
  state.day = day;
  switchView("sessions");
  state.daySessions = state.dayCache[day] || [];
  state.dayStatus = state.dayCache[day] ? "ready" : "loading";
  render();
  await loadDay();
  if (state.view === "sessions") render();
}

async function navigateView(view) {
  switchView(view);
  if (view === "sessions") {
    state.daySessions = state.dayCache[state.day] || [];
    state.dayStatus = state.dayCache[state.day] ? "ready" : "loading";
  }
  render();
  if (view === "goals") await act("generate_due_goals", {});
  if (view === "sessions") await loadDay();
  if (view === "graphs") await loadGraphs();
  if (state.view === view) render();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => navigateView(tab.dataset.view);
});

async function graphChanged() {
  state.graphs = null;
  saveGraphPreferences();
  renderGraphs();
  await loadGraphs();
  if (state.view === "graphs") renderGraphs();
}

document.querySelectorAll("#view-graphs .seg-btn").forEach((button) => {
  button.onclick = () => {
    if (button.dataset.mode) state.graphMode = button.dataset.mode;
    if (button.dataset.range) state.graphRange = button.dataset.range;
    if (button.dataset.group) state.graphGroup = button.dataset.group;
    if (state.graphRange === "custom" && !state.graphStart) {
      state.graphEnd = state.context?.today || localIso(new Date()).slice(0, 10);
      state.graphStart = addDays(state.graphEnd, -6);
    }
    graphChanged();
  };
});
$("graph-start").onchange = () => { state.graphStart = $("graph-start").value; graphChanged(); };
$("graph-end").onchange = () => { state.graphEnd = $("graph-end").value; graphChanged(); };
$("graph-options").onclick = graphOptionsSheet;
graphFullscreen = createFullscreenController($("view-graphs"), { onChange: (open) => {
  $("graph-fullscreen").textContent = open ? "Close fullscreen" : "Fullscreen";
  $("graph-fullscreen").setAttribute("aria-expanded", String(open));
  queueGraphResize();
} });
$("graph-fullscreen").onclick = () => graphFullscreen.toggle();
window.addEventListener("resize", queueGraphResize);
if (typeof ResizeObserver === "function") new ResizeObserver(queueGraphResize).observe($("view-graphs"));
$("subjects-active").onclick = () => { state.subjectFilter = "active"; renderToday(); };
$("subjects-archived").onclick = () => { state.subjectFilter = "archived"; renderToday(); };
$("refresh").onclick = () => refresh({ force: true });
$("open-settings").onclick = () => settingsSheet();
$("stop-btn").onclick = () => stopTimer();
$("day-prev").onclick = () => navigateDay(addDays(state.day, -1));
$("day-next").onclick = () => navigateDay(addDays(state.day, 1));
$("day-date").onchange = () => { if ($("day-date").value) navigateDay($("day-date").value); };
$("day-today").onclick = () => navigateDay(logicalAgendaNow(new Date(), dayStartHour({}, state.context?.day_start))?.day || state.context?.today);
$("add-session").onclick = () => addSessionSheet();
$("add-goal").onclick = () => addGoalSheet();
$("goal-templates").onclick = templatesSheet;
$("add-subject").onclick = () => addSubjectSheet();
$("goals-active").onclick = () => { state.goalsFilter = "active"; render(); };
$("goals-done").onclick = () => { state.goalsFilter = "done"; render(); };
$("sheet-close").onclick = closeSheet;
$("sheet").onclick = (event) => { if (event.target === $("sheet")) closeSheet(); };
$("setup-save").onclick = trySetup;
$("setup-token").onkeydown = (event) => { if (event.key === "Enter") trySetup(); };

// Coming back to the app is when stale data is most obvious, and the most
// likely moment to have regained signal.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopTicking();
  else {
    updateAgendaNow();
    queueGraphResize();
    if (getToken() && $("sheet").classList.contains("hidden")) refresh({ quiet: true });
    else paintElapsed();
  }
});

// The Mac syncs whenever its window gains focus, which is why a change made on
// the phone seems to appear there instantly. Without this the reverse was not
// true: a phone sitting open would not notice work done on the laptop. Only
// while actually on screen, so it costs nothing in the background.
setInterval(() => {
  if (!document.hidden) updateAgendaNow();
  const sheetOpen = !$("sheet").classList.contains("hidden");
  // Not while a sheet is open: a refresh redraws everything and would pull a
  // half-filled form out from under the user.
  if (!document.hidden && getToken() && !sheetOpen) refresh({ quiet: true });
}, 60000);
window.addEventListener("online", () => refresh({ quiet: true }));

// Register the service worker so the app opens with no signal. Failing to
// register is not worth surfacing: it only costs offline support.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

if (getToken()) {
  $("app").classList.remove("hidden");
  restoreMirror();
  if (!state.painted) render();
  refresh();
} else {
  showSetup();
}
