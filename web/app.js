// Startup, navigation and shared refresh coordination. Features own their UI state.
import { api } from "./data/api.js";
import { send, flush, pendingCount, pendingForProjection as pendingOperations, pendingTokenMatches, subscribeOutbox } from "./data/outbox.js";
import { getToken, setToken, clearToken } from "./data/credentials.js";
import { ApiError } from "./data/errors.js";
import { createActionRunner } from "./data/actions.js";
import { createMirror } from "./data/mirror.js";
import { createSyncStatus } from "./data/sync-status.js";
import { $ } from "./shared/dom.js";
import { banner, hideBanner } from "./shared/banner.js";
import { bindSheet, closeSheet } from "./shared/sheet.js";
import { createOrderControls } from "./shared/order-controls.js";
import { closeFullscreen } from "./shared/fullscreen.js";
import { STORAGE_KEYS } from "./shared/storage.js";
import { PROFILE, PROFILE_ID } from "./shared/profile.js";
import { createSubjectsFeature } from "./features/subjects/index.js";
import { createGoalsFeature } from "./features/goals/index.js";
import { createTemplatesFeature } from "./features/templates.js";
import { createSessionsFeature } from "./features/sessions/index.js";
import { createGraphsFeature } from "./features/graphs/index.js";
import { createSettingsFeature } from "./features/settings/index.js";
import { weightFeature } from "./weight.js";

const state = { view: PROFILE.defaultView, painted: false };
document.documentElement.dataset.profile = PROFILE_ID;
$("setup").querySelector("h1").textContent = PROFILE.name;
for (const tab of document.querySelectorAll(".tab")) {
  if (!PROFILE.views.includes(tab.dataset.view)) tab.remove();
}
const navigation = document.querySelector(".tabbar");
navigation.classList.toggle("five", PROFILE.views.length === 5);
navigation.classList.toggle("four", PROFILE.views.length === 4);
navigation.classList.toggle("hidden", PROFILE.views.length === 1);
const syncStatus = createSyncStatus({ getToken, pendingCount, onChange: (value) => {
  const node = $("sync-status");
  if (node.textContent !== value.label) node.textContent = value.label;
  node.dataset.phase = value.phase;
  node.title = value.detail;
} });
subscribeOutbox(syncStatus.outbox);
let sessions;
const mirror = createMirror({ api, pendingOperations, getDayCache: () => sessions.cache });
const { store } = mirror;
const getView = () => state.view;
const reorderButtons = createOrderControls({ act, closeSheet });
sessions = createSessionsFeature({ store, api, act, banner, saveMirror: mirror.save, navigateDay });
const subjects = createSubjectsFeature({ store, act, banner, reorderButtons, editSession: sessions.edit, quickAdd: sessions.quickAdd });
const goals = createGoalsFeature({ store, act, banner, render, getView, reorderButtons });
const templates = createTemplatesFeature({ store, act, send, banner });
const graphs = createGraphsFeature({ store, api, banner, getView, navigateDay, editSession: sessions.edit });
const settings = createSettingsFeature({ store, act, banner, flush, pendingCount, refresh, clearToken, getSyncStatus: syncStatus.snapshot, profile: PROFILE_ID });
const features = { today: subjects, goals, sessions, graphs, weight: { render: () => weightFeature.render($("weight-body")) } };
const VIEW_TITLES = { goals: "Goals", today: "Subjects", sessions: "Sessions", graphs: "Graphs", weight: "Weight" };

function restoreMirror() {
  // Weight owns its small credential-scoped cache. Keep the legacy time mirror
  // untouched for rollback, without loading years of unrelated tracking data.
  if (PROFILE_ID === "weight") return;
  try {
    const cached = mirror.restore();
    if (cached) {
      sessions.reset(cached.dayCache || {}, cached.context?.today || null);
      render(); state.painted = true;
    }
  } catch (error) { banner(error.message || "Could not read cached data", "error", 0); }
}
let refreshing = null, mutationRevision = 0, forceRequested = false, refreshRequested = false;
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

async function refreshOnce({ quiet = false, force = false } = {}) {
  syncStatus.begin({ quiet });
  const revision = mutationRevision;
  if (!quiet) $("refresh").setAttribute("aria-busy", "true");
  let changedThisPass = !state.painted;
  try {
    const outgoing = await flush();
    if (outgoing.offline) throw new ApiError(outgoing.error);
    const context = await api.context();
    if (PROFILE_ID === "weight") {
      changedThisPass = force || context.head !== store.head || !state.painted;
      store.context = context;
      store.head = context.head;
      if (changedThisPass) weightFeature.invalidate();
      if (!outgoing.ok) {
        syncStatus.failure(outgoing);
        banner(`Changes waiting: ${outgoing.error}`, "error", 0);
      } else { syncStatus.success(); hideBanner(); }
      return;
    }
    const followingToday = !sessions.day || sessions.day === store.context?.today;
    const changed = force || !store.baseSnapshot || context.head !== store.head ||
      context.day_start !== store.context?.day_start || context.today !== store.context?.today;
    changedThisPass = changed || store.active?.uid !== context.active?.uid ||
      store.active?.start_time !== context.active?.start_time;
    if (changed) await mirror.update(force, context);
    store.context = context;
    store.baseActive = context.active ? { ...context.active,
      timer_origin: Date.now() - (context.active.elapsed_seconds || 0) * 1000 } : null;
    if (revision === mutationRevision) mirror.project();
    else refreshRequested = true;
    if (followingToday) sessions.day = context.today;
    if (changed) {
      sessions.invalidate();
      graphs.invalidate();
      weightFeature.invalidate();
    }
    if (store.active && sessions.day === context.today) delete sessions.cache[sessions.day];
    if (state.view === "sessions" && (changed || sessions.status !== "ready")) await sessions.load();
    if (state.view === "graphs" && (changed || store.active || !graphs.data)) await graphs.load();
    if (changed) mirror.save();
    if (!outgoing.ok) {
      syncStatus.failure(outgoing);
      banner(`Changes waiting: ${outgoing.error}`, "error", 0);
    } else { syncStatus.success(); hideBanner(); }
  } catch (err) {
    syncStatus.failure(err);
    if (err instanceof ApiError && err.status === 401) return showSetup("That token was rejected.");
    let waiting = 0;
    try { waiting = pendingCount(); mirror.project(); } catch (storageError) { err = storageError; }
    const showing = store.baseSnapshot ? ", showing saved data" : "";
    banner(err.offline
      ? `Offline${waiting ? ` — ${waiting} change(s) waiting` : ""}${showing}`
      : `Problem: ${err.message}`, "error", 0);
    changedThisPass = true;
  } finally {
    $("refresh").removeAttribute("aria-busy");
    if (changedThisPass || !state.painted) {
      state.painted = true;
      render();
    } else paintElapsed();
  }
}

const runAction = createActionRunner({ send, project: mirror.project, render, refresh, banner,
  onMutation: () => mutationRevision++ });
function act(op, params, options = {}) { return runAction(op, params, options); }

function render() {
  syncStatus.paint();
  $("view-title").textContent = VIEW_TITLES[state.view];
  for (const view of Object.keys(VIEW_TITLES)) {
    $(`view-${view}`).classList.toggle("hidden", view !== state.view);
  }
  document.querySelectorAll(".tab").forEach((t) => {
    const selected = t.dataset.view === state.view;
    t.classList.toggle("on", selected);
    t.setAttribute("aria-current", selected ? "page" : "false");
  });

  features[state.view]?.render();
  if (state.view !== "today") subjects.deactivate();
  if (state.scrollTarget !== undefined) {
    $("views").scrollTop = state.scrollTarget;
    state.scrollTarget = undefined;
  }
}

function showSetup(message) {
  syncStatus.paint();
  closeFullscreen();
  subjects.deactivate();
  $("app").classList.add("hidden");
  $("setup").classList.remove("hidden");
  $("setup-error").textContent = message || "";
}

async function trySetup() {
  const token = $("setup-token").value.trim();
  if (!token) return;
  try {
    const previous = getToken();
    if (pendingCount() && !pendingTokenMatches(token)) {
      throw new ApiError("Send the waiting changes using the original token before connecting with another token.", 409);
    }
    setToken(token);
    await api.context();
    if (previous !== token) {
      closeFullscreen();
      state.view = PROFILE.defaultView;
      goals.reset();
      mirror.reset(); sessions.reset(); graphs.reset();
      viewScroll.clear();
      weightFeature.invalidate();
      try {
        localStorage.removeItem(STORAGE_KEYS.mirror);
        localStorage.removeItem(STORAGE_KEYS.weightCache);
      } catch { /* account-tagged caches cannot be reused by another token */ }
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

function switchView(view) {
  if (!PROFILE.views.includes(view)) return;
  if (state.view !== view) {
    closeFullscreen();
    viewScroll.set(state.view, $("views").scrollTop);
    state.view = view;
    state.scrollTarget = viewScroll.get(view) || 0;
  }
}

async function navigateDay(day) {
  if (!PROFILE.views.includes("sessions")) return;
  sessions.prepare(day);
  switchView("sessions");
  render();
  await sessions.load();
  if (state.view === "sessions") render();
}

async function navigateView(view) {
  if (!PROFILE.views.includes(view)) return;
  switchView(view);
  if (view === "sessions") sessions.prepare();
  render();
  if (view === "goals") await act("generate_due_goals", {});
  if (view === "sessions") await sessions.load();
  if (view === "graphs") await graphs.load();
  if (state.view === view) render();
}
function paintElapsed() { if (state.view === "today") subjects.paintElapsed(); }
const viewScroll = new Map();
for (const feature of [subjects, goals, templates, sessions, graphs]) feature.mount();
bindSheet();
document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => navigateView(tab.dataset.view); });
$("refresh").onclick = () => refresh({ force: true });
$("open-settings").onclick = settings.open;
$("sync-status").onclick = settings.open;
$("setup-save").onclick = trySetup;
$("setup-token").onkeydown = (event) => { if (event.key === "Enter") trySetup(); };
// Coming back to the app is when stale data is most obvious, and the most
// likely moment to have regained signal.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) subjects.deactivate();
  else {
    graphs.clock();
    graphs.resize();
    if (getToken() && $("sheet").classList.contains("hidden")) refresh({ quiet: true });
    else paintElapsed();
  }
});

// The Mac syncs whenever its window gains focus, which is why a change made on
// the phone seems to appear there instantly. Without this the reverse was not
// true: a phone sitting open would not notice work done on the laptop. Only
// while actually on screen, so it costs nothing in the background.
setInterval(() => {
  if (!document.hidden) graphs.clock();
  const sheetOpen = !$("sheet").classList.contains("hidden");
  // Not while a sheet is open: a refresh redraws everything and would pull a
  // half-filled form out from under the user.
  if (!document.hidden && getToken() && !sheetOpen) refresh({ quiet: true });
}, 60000);
window.addEventListener("online", () => refresh({ quiet: true }));
window.addEventListener("offline", () => syncStatus.failure({ offline: true, message: "Waiting for connection." }));
window.addEventListener("storage", (event) => {
  if (event.key === STORAGE_KEYS.outbox || event.key === STORAGE_KEYS.syncStatus) syncStatus.paint();
  if (event.key !== STORAGE_KEYS.token && event.key !== null) return;
  closeSheet(); closeFullscreen();
  goals.reset(); mirror.reset(); sessions.reset(); graphs.reset();
  viewScroll.clear(); weightFeature.invalidate();
  state.view = PROFILE.defaultView; state.painted = false;
  const token = getToken();
  try {
    if (!token) return showSetup();
    if (!pendingTokenMatches(token)) return showSetup("Saved changes belong to another connection. Reconnect the original token to send them.");
    $("setup").classList.add("hidden"); $("app").classList.remove("hidden");
    restoreMirror(); render(); refresh({ quiet: true });
  } catch (error) { showSetup(error.message); }
});

// Register the service worker so the app opens with no signal. Failing to
// register is not worth surfacing: it only costs offline support.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

try {
  if (getToken() && pendingTokenMatches(getToken())) {
    $("app").classList.remove("hidden");
    restoreMirror();
    if (!state.painted) render();
    refresh();
  } else {
    showSetup(getToken() ? "Saved changes belong to another connection. Reconnect the original token to send them." : "");
  }
} catch (error) { showSetup(error.message); }
