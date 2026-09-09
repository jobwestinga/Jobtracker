// JobTracker on the phone.
//
// Deliberately plain ES modules — no framework, no bundler, nothing to install
// to change a line. It renders what the server computes and never re-implements
// a rule: the logical day, the milestone gate and the sub-30-second rule all
// stay on the server, where the desktop app's own code enforces them.

import { api, send, flush, pendingCount, getToken, setToken, clearToken, ApiError, uuid } from "./api.js";

const $ = (id) => document.getElementById(id);
const state = {
  view: "today",
  snapshot: null,
  head: null,
  context: null,
  active: null,
  day: null,
  daySessions: [],
  goalsFilter: "active",
  painted: false,
  expandedGoal: null,
  graphMode: "bars",
  graphRange: "7",
  graphGroup: "auto",
  graphs: null,
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

async function refresh({ quiet = false, force = false } = {}) {
  if (!quiet) $("refresh").textContent = "…";
  let changedThisPass = true;   // assume a repaint unless we prove otherwise
  try {
    await flush();

    // Context is a few dozen bytes and carries the server's change counter and
    // the running timer. The snapshot is ~half a megabyte, so it is only
    // re-fetched when that counter actually moved. An idle minute-poll used to
    // pull the whole database down again.
    const context = await api.context();
    const changed = force || !state.snapshot || context.head !== state.head;
    // The running timer still has to move even when nothing else did.
    changedThisPass = changed || Boolean(state.active) !== Boolean(context.active);
    if (changed) {
      state.snapshot = await api.snapshot();
      state.head = context.head;
    }
    state.context = context;
    state.active = context.active
      ? { ...context.active, elapsed_seconds: context.active.elapsed_seconds }
      : null;
    if (!state.day) state.day = context.today;
    if (state.view === "sessions" && (changed || !state.daySessions.length)) {
      await loadDay();
    }
    if (state.view === "graphs" && (changed || !state.graphs)) await loadGraphs();
    hideBanner();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showSetup("That token was rejected.");
    const waiting = pendingCount();
    const showing = state.snapshot ? ", showing the last data" : "";
    banner(
      err.offline
        ? `Offline${waiting ? ` — ${waiting} change(s) waiting` : ""}${showing}`
        : `Problem: ${err.message}`,
      "error",
      0,
    );
  } finally {
    $("refresh").textContent = "↻";
    // Repaint only when there is something new to show. A quiet minute-poll
    // used to rebuild every list from scratch for no reason, which on the goals
    // tab meant discarding and recreating every card.
    if (changedThisPass || !state.painted) {
      state.painted = true;
      render();
    } else {
      paintElapsed();
    }
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

/**
 * Apply a write.
 *
 * `optimistic` updates local state and repaints BEFORE the request goes out, so
 * a tap feels instant instead of waiting a round trip. The server is still the
 * only authority: the refresh that follows overwrites whatever we guessed, and
 * on failure it puts the truth back on screen.
 */
async function act(op, params, options = {}) {
  const { optimistic, uid } = options;
  if (optimistic) {
    try { optimistic(); render(); } catch { /* never let a guess break the UI */ }
  }
  try {
    const outcome = await send(op, params, uid ? { uid } : {});
    if (outcome.queued) banner("Saved — will sync when you're back online", "ok");
    await refresh({ quiet: true, force: true });
    return outcome;
  } catch (err) {
    banner(err.message || "That did not work");
    await refresh({ quiet: true, force: true });
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

const VIEW_TITLES = { goals: "Goals", today: "Subjects", sessions: "Sessions", graphs: "Graphs" };

function render() {
  $("view-title").textContent = VIEW_TITLES[state.view];
  for (const view of Object.keys(VIEW_TITLES)) {
    $(`view-${view}`).classList.toggle("hidden", view !== state.view);
  }
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("on", t.dataset.view === state.view));

  if (state.view === "today") renderToday();
  if (state.view === "sessions") renderSessions();
  if (state.view === "goals") renderGoals();
  if (state.view === "graphs") renderGraphs();
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

function paintElapsed() {
  if (state.view === "today" && state.active) startTicking();
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

function renderGoals() {
  $("goals-active").classList.toggle("on", state.goalsFilter === "active");
  $("goals-done").classList.toggle("on", state.goalsFilter === "done");

  const list = $("goal-list");
  list.innerHTML = "";
  const goals = (state.snapshot?.goals || [])
    .filter((g) => (state.goalsFilter === "done" ? g.is_completed : !g.is_completed))
    .sort((a, b) => {
      // Focused goals first: they are the ones being worked on right now.
      const focus = (b.is_focused ? 1 : 0) - (a.is_focused ? 1 : 0);
      return focus || (a.sort_order || 0) - (b.sort_order || 0);
    });

  if (!goals.length) {
    list.append(el("p", "muted",
      state.goalsFilter === "done" ? "No completed goals yet." : "No goals yet."));
    return;
  }

  for (const goal of goals) {
    list.append(goalCard(goal));
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
  star.onclick = (event) => {
    event.stopPropagation();
    act("toggle_goal_focused", { goal_uid: goal.uid },
      { optimistic: () => patchGoal(goal.uid, { is_focused: goal.is_focused ? 0 : 1 }) });
  };

  head.append(check, body, star);
  wrap.append(head);

  if (expanded) {
    const panel = el("div", "goal-panel");
    if (goal.notes) panel.append(el("p", "muted goal-notes", goal.notes));

    for (const milestone of items) {
      const row = el("div", "ms-row");
      const box = el("button", `check small ${milestone.is_done ? "done" : ""}`, "✓");
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
      { type: "text", placeholder: "Add milestone" });
    const addBtn = el("button", "secondary", "+");
    addBtn.onclick = () => {
      const title = input.value.trim();
      if (!title) return;
      input.value = "";
      act("add_milestone", { goal_uid: goal.uid, title }, { uid: uuid() });
    };
    input.onkeydown = (event) => { if (event.key === "Enter") addBtn.onclick(); };
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

async function loadGraphs() {
  const range = Number(state.graphRange);
  try {
    if (state.graphMode === "agenda") {
      // The agenda paints clock positions, so a year of columns is meaningless;
      // the endpoint caps it and the result simply scrolls.
      state.graphs = { kind: "agenda", data: await api.agenda(Math.min(range, 60)) };
    } else {
      const grouping = state.graphGroup === "auto"
        ? automaticGrouping(range)
        : state.graphGroup;
      state.graphs = { kind: "bars", data: await api.breakdown(grouping, range) };
    }
  } catch (err) {
    state.graphs = { kind: "error", message: err.message };
  }
}

function renderGraphs() {
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
  host.innerHTML = "";
  $("graph-legend").innerHTML = "";

  if (!state.graphs) {
    host.append(el("div", "spinner", "Loading…"));
    return;
  }
  if (state.graphs.kind === "error") {
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
      piece.style.background = segment.color || "var(--accent)";
      piece.title = `${segment.name}: ${hm(segment.seconds)}`;
      stack.append(piece);
    }
    const value = el("div", "bar-val", bucket.total_seconds ? hm(bucket.total_seconds) : "");
    const label = el("div", "bar-lbl", shortLabel(bucket.date, data.grouping));
    column.append(value, stack, label);
    // Tapping a day opens exactly that day, the way the desktop's charts do.
    if (data.grouping === "daily") {
      column.onclick = async () => {
        state.day = bucket.date;
        state.view = "sessions";
        await loadDay();
        render();
      };
    }
    chart.append(column);
  }
  host.append(chart);

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
      dot.style.background = entry.color;
      chip.append(dot, el("span", null, `${name} · ${hm(entry.seconds)}`));
      legend.append(chip);
    });
}


function drawAgenda(host, data) {
  const days = data.days || [];
  const sessions = data.sessions || [];
  const total = sessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);
  $("graph-total").textContent = `${hm(total)} across ${days.length} days`;

  if (!sessions.length) {
    host.append(el("p", "muted", "Nothing tracked in this range."));
    return;
  }

  // Fit the window to the work actually done, but never narrower than a normal
  // day, so a single early session does not stretch one block over the screen.
  const lo = Math.min(6, Math.floor(Math.min(...sessions.map((s) => s.start_h))));
  const hi = Math.max(23, Math.ceil(Math.max(...sessions.map((s) => s.end_h))));
  const span = Math.max(1, hi - lo);
  const height = 300;
  const yFor = (hour) => ((hour - lo) / span) * height;

  const wrap = el("div", "agenda-wrap");

  const gutter = el("div", "agenda-hours");
  gutter.style.height = `${height}px`;
  for (let hour = Math.ceil(lo); hour <= hi; hour += span > 12 ? 3 : 2) {
    const mark = el("span", null, `${pad(hour % 24)}:00`);
    mark.style.top = `${yFor(hour)}px`;
    gutter.append(mark);
  }
  wrap.append(gutter);

  const scroll = el("div", "agenda-scroll");
  const grid = el("div", "agenda-grid");
  const byDay = new Map(days.map((d) => [d, []]));
  for (const session of sessions) {
    if (byDay.has(session.day)) byDay.get(session.day).push(session);
  }

  for (const day of days) {
    const column = el("div", "agenda-col");
    const track = el("div", "agenda-track");
    track.style.height = `${height}px`;

    for (const session of byDay.get(day)) {
      const top = yFor(session.start_h);
      const blockHeight = Math.max(3, yFor(session.end_h) - top);
      const block = el("div", "agenda-block");
      block.style.top = `${top}px`;
      block.style.height = `${blockHeight}px`;
      block.style.borderLeftColor = session.color || "var(--accent)";
      // Neutral base plus a tint of the subject colour, as on the desktop.
      block.style.background = `color-mix(in srgb, ${session.color || "#3B82F6"} 35%, var(--panel-2))`;
      if (blockHeight > 16) block.textContent = session.subject_name;
      block.title = `${session.subject_name} · ${hm(session.duration_seconds)}`;
      track.append(block);
    }

    // Tapping a day opens it in Sessions, where it can actually be edited.
    track.onclick = async () => {
      state.day = day;
      state.view = "sessions";
      await loadDay();
      render();
    };

    const label = el("div", "agenda-daylbl", prettyDayShort(day));
    if (day === state.context?.today) label.classList.add("today");
    column.append(track, label);
    grid.append(column);
  }

  scroll.append(grid);
  wrap.append(scroll);
  host.append(wrap);
  // Most recent days matter most, so start at the right-hand edge.
  requestAnimationFrame(() => { scroll.scrollLeft = scroll.scrollWidth; });
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
    body.append(el("label", null, "Quick add, ending now"), quick);

    const save = el("button", "primary wide", "Add with these times");
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



function addSubjectSheet() {
  openSheet("New subject", (body) => {
    const name = field(body, "Name",
      Object.assign(document.createElement("input"),
        { type: "text", placeholder: "e.g. Thesis" }));

    body.append(el("label", null, "Colour"));
    // The desktop suggests colours that stay distinct from the existing ones;
    // on the phone a fixed palette is enough, with the already-used ones marked.
    const used = new Set((state.snapshot?.subjects || []).map((s) => s.color));
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
      await act("set_setting", { key: "day_start_time", value: daySelect.value });
      closeSheet();
      banner("Day start updated — this changes it on your Mac too", "ok");
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
        : "Still offline — will keep trying", outcome.ok ? "ok" : "error");
      closeSheet();
      refresh({ quiet: true });
    };
    body.append(retry);

    const reload = el("button", "secondary wide", "Reload app");
    reload.onclick = () => location.reload();
    body.append(reload);

    const out = el("button", "danger wide", "Disconnect this phone");
    out.onclick = () => {
      if (!confirm("Remove the token from this phone? Your data stays on the server.")) return;
      clearToken();
      location.reload();
    };
    body.append(out);
  });
}

// ── timer actions ───────────────────────────────────────────────────────

async function startTimer(subject) {
  if (state.active) {
    if (!confirm(`Switch to ${subject.name}?`)) return;
    await act("switch_subject", { subject_uid: subject.uid }, {
      optimistic: () => { state.active = { subject_uid: subject.uid, elapsed_seconds: 0 }; },
    });
    return;
  }
  // Show the timer running the moment it is tapped. Waiting for the round trip
  // made a start feel like it had not registered.
  await act("start_subject", { subject_uid: subject.uid }, {
    uid: uuid(),
    optimistic: () => { state.active = { subject_uid: subject.uid, elapsed_seconds: 0 }; },
  });
}

async function stopTimer() {
  // Send the phone's clock, so the server applies the sub-30-second rule to the
  // time the user actually stopped rather than to whenever the request lands.
  await act("stop_active_subject", { end_time: localIso(new Date()) }, {
    optimistic: () => { state.active = null; },
  });
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
    if (state.view === "graphs") {
      state.graphs = null;
      render();          // paint the spinner first
      await loadGraphs();
    }
    render();
  };
});

document.querySelectorAll("#view-graphs .seg-btn").forEach((button) => {
  button.onclick = async () => {
    if (button.dataset.mode) state.graphMode = button.dataset.mode;
    if (button.dataset.range) state.graphRange = button.dataset.range;
    if (button.dataset.group) state.graphGroup = button.dataset.group;
    state.graphs = null;
    render();
    await loadGraphs();
    render();
  };
});
$("refresh").onclick = () => refresh({ force: true });
$("open-settings").onclick = () => settingsSheet();
$("stop-btn").onclick = () => stopTimer();
$("day-prev").onclick = async () => { state.day = addDays(state.day, -1); await loadDay(); render(); };
$("day-next").onclick = async () => { state.day = addDays(state.day, 1); await loadDay(); render(); };
$("add-session").onclick = () => addSessionSheet();
$("add-goal").onclick = () => addGoalSheet();
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
  if (!document.hidden && getToken()) refresh({ quiet: true });
});

// The Mac syncs whenever its window gains focus, which is why a change made on
// the phone seems to appear there instantly. Without this the reverse was not
// true: a phone sitting open would not notice work done on the laptop. Only
// while actually on screen, so it costs nothing in the background.
setInterval(() => {
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
  refresh();
} else {
  showSetup();
}
