import { api, send, uuid, getToken, pendingOperations } from "./api.js";
import { createFullscreenController } from "./fullscreen.js";
import { parseWeight, selectMeasurements, pointsFor, trendFor, wholeWeightTiles,
  weightTileAnchor, splitWeightTiles, selectedTileWeight, dailyWeightMoment, manualDateCollision } from "./weight-utils.js";

const CACHE = "jobtracker.weight-cache", PREFS = "jobtracker.weight-preferences";
const model = { rows: [], today: "", loaded: false, loading: null, token: null, editing: null,
  limit: 12, version: 0, epoch: 0, mutation: 0, draft: 0, cleanDraft: 0,
  calendar: "", automaticDate: true, saving: null, deleting: null,
  picker: { anchor: null, whole: null, decimal: null, original: null, changed: false } };
let preferences;
try { preferences = JSON.parse(localStorage.getItem(PREFS) || "{}"); } catch { preferences = {}; }
if (!preferences || typeof preferences !== "object") preferences = {};
preferences = {
  range: "90", trend: "loess", span: "0.04", points: true, historic: false,
  zero: false, start: "", end: "", density: "comfortable", contrast: "normal", ...preferences,
};
if (!["30", "90", "180", "365", "all", "custom"].includes(preferences.range)) preferences.range = "90";
if (!["loess", "mean7", "none"].includes(preferences.trend)) preferences.trend = "loess";
if (!["0.04", "0.08", "0.15"].includes(String(preferences.span))) preferences.span = "0.04";
for (const key of ["points", "historic", "zero"]) if (typeof preferences[key] !== "boolean") preferences[key] = key === "points";
for (const key of ["start", "end"]) if (!/^\d{4}-\d{2}-\d{2}$/.test(preferences[key])) preferences[key] = "";
if (!["comfortable", "compact"].includes(preferences.density)) preferences.density = "comfortable";
if (!["normal", "high"].includes(preferences.contrast)) preferences.contrast = "normal";
let host, chartObserver, fullscreen, drawnVersion = -1;

const node = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};
const field = (id) => host.querySelector(`#${id}`);
const localDate = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};
const formatDay = (value) => new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function status(text, error = false) {
  const output = field("weight-status");
  output.textContent = text;
  output.classList.toggle("error", error);
}
function clearSavedReceipt() {
  const output = field("weight-status");
  if (!output.classList.contains("error") && /^Saved\.$|^Previous weighing saved\.(?: Current selection is unsaved\.)?$/.test(output.textContent)) status("");
}
const savedReceipt = () => unsavedSelection() ? "Previous weighing saved. Current selection is unsaved." : "Saved.";
function cache() {
  try { localStorage.setItem(CACHE, JSON.stringify({ owner: accountTag(model.token), rows: model.rows, today: model.today })); } catch { /* send() verifies durable write storage */ }
}
function accountTag(token) {
  // Keep cached data scoped to its credential without copying the credential
  // into another storage value. Two independent 32-bit hashes avoid accidental
  // cross-account cache reuse; the server still verifies every request.
  let a = 2166136261, b = 5381;
  for (const character of token || "") {
    a = Math.imul(a ^ character.charCodeAt(0), 16777619);
    b = Math.imul(b, 33) ^ character.charCodeAt(0);
  }
  return `${a >>> 0}:${b >>> 0}`;
}
const current = (epoch, token) => Boolean(host && model.epoch === epoch && model.token === token && getToken() === token);
function patch(rows, entry) {
  if (entry.op === "delete_weight") return rows.filter((r) => r.uid !== entry.params.weight_uid);
  if (entry.op !== "upsert_weight") return rows;
  const p = entry.params;
  let match = rows.find((r) => r.uid === (p.weight_uid || p.uid));
  if (!match && !p.weight_uid) match = rows.findLast((r) => r.source === "manual" && r.measured_at.slice(0, 10) === p.measured_at.slice(0, 10));
  if (!match && p.weight_uid) return rows;
  const measuredAt = p.measured_at.length === 10 ? `${p.measured_at}T00:00:00` : p.measured_at;
  const updated = { ...match, uid: match?.uid || p.uid, measured_at: measuredAt, weight_kg: p.weight_kg, source: match?.source || "manual", pending: true };
  return [...rows.filter((r) => r !== match), updated].sort((a, b) => a.measured_at.localeCompare(b.measured_at));
}
function overlay(rows) {
  return pendingOperations().reduce(patch, rows);
}

export function invalidateWeight() { model.loaded = false; }
export function resetWeight() {
  model.rows = []; model.loaded = false; model.today = ""; model.token = null;
  model.editing = null; model.loading = null; model.saving = null; model.deleting = null;
  model.version++; model.epoch++; model.draft++;
  model.calendar = ""; model.automaticDate = true; model.cleanDraft = model.draft;
  resetPicker();
  try { localStorage.removeItem(CACHE); } catch { /* ignore */ }
  fullscreen?.destroy(); fullscreen = null;
  host = null; chartObserver?.disconnect();
}

export function renderWeight(container) {
  const token = getToken();
  if (model.token !== token) {
    model.rows = []; model.loaded = false; model.editing = null; model.loading = null;
    model.today = ""; model.limit = 12; model.saving = null; model.deleting = null;
    model.token = token;
    model.epoch++; model.draft++;
    model.calendar = ""; model.automaticDate = true; model.cleanDraft = model.draft;
    resetPicker();
    fullscreen?.destroy(); fullscreen = null;
    host = null; chartObserver?.disconnect();
    try {
      const stored = JSON.parse(localStorage.getItem(CACHE) || "null");
      if (stored?.owner === accountTag(token) && Array.isArray(stored?.rows)) model.rows = stored.rows;
    } catch { /* no saved mirror */ }
    model.version++;
  }
  if (host !== container || !container.querySelector("#weight-entry")) initialize(container);
  checkCalendarDay();
  paint();
  loadWeight();
}

function loadWeight() {
  if (!host || model.loaded || model.loading || !model.token) return;
  const requestToken = model.token, epoch = model.epoch, revision = model.mutation, draft = model.draft,
    requestDay = localDate();
  const request = api.weights().then((data) => {
      if (!current(epoch, requestToken)) return;
      if (revision !== model.mutation) { model.loaded = false; return; }
      if (!Array.isArray(data.measurements)) throw new Error("The weight history response was incomplete; saved history is kept.");
      model.rows = overlay(data.measurements);
      model.today = requestDay === localDate() ? data.today || localDate() : localDate();
      model.loaded = true; model.version++;
      if (draft === model.draft && model.automaticDate && !model.editing && !unsavedSelection()) field("weight-date").value = model.today;
      cache();
      const waiting = pendingOperations().filter((p) => p.op.endsWith("weight")).length;
      if (waiting) status(`${waiting} weight change(s) waiting to sync`);
      else {
        const message = field("weight-status").textContent;
        if (/^(Saved on this phone|Previous weighing saved on this phone|\d+ weight change\(s\) waiting to sync)/.test(message)) status(savedReceipt());
        else if (message.startsWith("Deleted on this phone")) status("Deleted.");
        else if (/^(Offline|Connect|Reconnect)/.test(message)) status("");
      }
      paint();
    }).catch((err) => {
      if (!current(epoch, requestToken)) return;
      if (revision !== model.mutation) { model.loaded = false; return; }
      model.loaded = true;
      try { model.rows = overlay(model.rows); } catch (error) { status(error.message, true); return; }
      status(err.status === 401 ? "Reconnect in Settings to load your weight history." :
        (model.rows.length ? "Offline — showing saved weight history." : "Connect to load your weight history. You can still save a weighing."), true);
      model.version++; paint();
    }).finally(() => {
      if (model.loading !== request) return;
      model.loading = null;
      if (current(epoch, requestToken) && !model.loaded) loadWeight();
    });
  model.loading = request;
}

function initialize(container) {
  fullscreen?.destroy(); fullscreen = null;
  chartObserver?.disconnect(); host = container; drawnVersion = -1;
  container.innerHTML = `
    <form id="weight-entry" class="weight-entry">
      <fieldset class="weight-tile-group"><legend class="weight-visually-hidden">Whole kilograms</legend>
        <div id="weight-whole" class="weight-tile-row whole" role="group" aria-label="Whole kilograms"></div>
      </fieldset>
      <fieldset class="weight-tile-group"><legend class="weight-visually-hidden">Decimal part</legend>
        <div id="weight-decimal" class="weight-tile-row decimal" role="group" aria-label="Decimal part"></div>
      </fieldset>
      <div class="weight-chosen-row">
        <output id="weight-value" aria-label="Selected weight" aria-live="polite">—.— kg</output>
        <button id="weight-save" class="primary" type="submit" disabled>Save</button>
      </div>
      <p id="weight-picker-hint" class="muted" aria-live="polite"></p>
      <p id="weight-status" class="muted" role="status" aria-live="polite"></p>
      <details id="weight-entry-controls" class="weight-entry-options"><summary><span>Entry controls</span><span id="weight-entry-day" class="muted"></span></summary>
        <div class="weight-date-row">
          <label for="weight-date" class="muted">Date</label><input id="weight-date" type="date" required>
          <button id="weight-today" class="ghost" type="button">Today</button>
        </div>
        <div class="weight-anchor-controls"><span class="muted">Other weights</span><button id="weight-lower" type="button" class="ghost" aria-label="Show lower weights">‹</button><button id="weight-higher" type="button" class="ghost" aria-label="Show higher weights">›</button></div>
        <div class="weight-interface-options">
          <label>Tile size<select id="weight-density"><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
          <label>Contrast<select id="weight-contrast"><option value="normal">Normal</option><option value="high">High contrast</option></select></label>
        </div>
        <button id="weight-clear" type="button" class="ghost wide">Clear selection</button>
        <div id="weight-edit-actions" class="hidden weight-edit-actions">
          <button id="weight-cancel" class="ghost" type="button">Cancel edit</button>
          <button id="weight-delete" class="ghost error" type="button">Delete entry</button>
        </div>
      </details>
    </form>
    <div id="weight-summary" class="weight-summary"></div>
    <div id="weight-plot" class="weight-plot">
      <div class="weight-plot-toolbar"><h2>Weight over time</h2><select id="weight-range" aria-label="Plot date range">
        <option value="30">30 days</option><option value="90">90 days</option><option value="180">6 months</option><option value="365">1 year</option><option value="all">All time</option><option value="custom">Custom</option>
      </select><button id="weight-fullscreen" class="ghost" type="button" aria-label="Open weight plot fullscreen" aria-pressed="false">⛶</button></div>
      <div id="weight-custom" class="weight-custom hidden">
        <label>From<input id="weight-start" type="date"></label><label>To<input id="weight-end" type="date"></label>
      </div>
      <canvas id="weight-canvas" role="img" aria-label="Body weight over time"></canvas>
      <p id="weight-chart-detail" class="muted" aria-live="polite">Tap the plot to inspect a weighing.</p>
      <details class="weight-options"><summary>Plot options</summary>
        <label>Trend<select id="weight-trend"><option value="loess">LOESS (original plot)</option><option value="mean7">7-day average</option><option value="none">No trend</option></select></label>
        <label>Smoothing<select id="weight-span"><option value="0.04">Original (4%)</option><option value="0.08">Smoother (8%)</option><option value="0.15">Broad (15%)</option></select></label>
        <label class="weight-check"><input id="weight-points" type="checkbox">Show measurements</label>
        <label class="weight-check"><input id="weight-historic" type="checkbox">Include historic measurements</label>
        <label class="weight-check"><input id="weight-zero" type="checkbox">Start weight axis at zero</label>
        <p class="muted">The historic curve interpolates between sparse measurements.</p>
        <div class="weight-export"><button id="weight-png" type="button" class="ghost">Save plot</button><button id="weight-csv" type="button" class="ghost">Export data</button></div>
      </details>
    </div>
    <h2 class="weight-history-title">Recent weighings</h2><div id="weight-history" class="list"></div>
    <button id="weight-more" class="ghost wide" type="button">Show older weighings</button>`;
  field("weight-date").value = model.today || localDate();
  field("weight-entry").addEventListener("submit", save);
  field("weight-date").addEventListener("change", () => { model.draft++; clearSavedReceipt(); model.automaticDate = field("weight-date").value === localDate(); if (!model.editing) field("weight-save").textContent = "Save"; paintPicker(); });
  field("weight-clear").onclick = () => { resetPicker(); model.draft++; clearSavedReceipt(); paintPicker(); };
  field("weight-lower").onclick = () => shiftPicker(-7);
  field("weight-higher").onclick = () => shiftPicker(7);
  field("weight-today").onclick = () => { cancelEdit(); field("weight-date").value = localDate(); };
  field("weight-cancel").onclick = cancelEdit;
  field("weight-delete").onclick = deleteEntry;
  field("weight-more").onclick = () => { model.limit += 30; paintHistory(); };
  for (const key of ["range", "trend", "span", "points", "historic", "zero", "start", "end", "density", "contrast"]) {
    const input = field(`weight-${key}`);
    if (input.type === "checkbox") input.checked = Boolean(preferences[key]);
    else input.value = preferences[key];
    input.addEventListener("change", () => {
      preferences[key] = input.type === "checkbox" ? input.checked : input.value;
      try { localStorage.setItem(PREFS, JSON.stringify(preferences)); } catch { /* optional preferences */ }
      if (key === "density" || key === "contrast") paintPicker();
      else { drawnVersion = -1; paintChart(); }
    });
  }
  field("weight-png").onclick = () => field("weight-canvas").toBlob((blob) => { if (blob) download(blob, "weight-plot.png"); });
  field("weight-csv").onclick = () => {
    const rows = ["measured_at,weight_kg,source", ...model.rows.map((r) => `${r.measured_at},${Number(r.weight_kg)},${r.source}`)];
    download(new Blob([rows.join("\r\n")], { type: "text/csv;charset=utf-8" }), "weight-data.csv");
  };
  fullscreen = createFullscreenController(field("weight-plot"), { onChange: (open) => {
    if (!host) return;
    const button = field("weight-fullscreen");
    button.textContent = open ? "×" : "⛶";
    button.setAttribute("aria-label", open ? "Close weight plot fullscreen" : "Open weight plot fullscreen");
    button.setAttribute("aria-pressed", String(open));
    drawnVersion = -1;
    requestAnimationFrame(() => { if (host) paintChart(); });
  } });
  field("weight-fullscreen").onclick = () => fullscreen.toggle();
  chartObserver = new ResizeObserver(() => { drawnVersion = -1; if (host && !host.classList.contains("hidden")) paintChart(); });
  chartObserver.observe(field("weight-canvas"));
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob), link = node("a");
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function resetPicker() {
  model.picker = { anchor: null, whole: null, decimal: null, original: null, changed: false, shifted: false };
}

function unsavedSelection() {
  return model.draft !== model.cleanDraft && (model.picker.whole !== null || model.picker.decimal !== null);
}

function checkCalendarDay() {
  if (!host || model.token !== getToken()) return;
  const today = localDate(), previous = model.calendar;
  model.calendar = today;
  if (!previous || previous === today) return;
  model.today = today;
  if (model.automaticDate && !model.editing && !unsavedSelection()) field("weight-date").value = today;
  model.loaded = false;
  loadWeight();
}
// Weight entry follows calendar midnight independently of the timer's 03:00
// logical day. Check on resume and while open without repainting the plot.
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkCalendarDay(); });
window.addEventListener("pageshow", checkCalendarDay);
setInterval(() => { if (!document.hidden) checkCalendarDay(); }, 60000);

function chosenWeight() {
  if (!model.picker.changed && parseWeight(model.picker.original) !== null &&
      model.picker.whole !== null && model.picker.decimal !== null) return model.picker.original;
  const selected = selectedTileWeight(model.picker.whole, model.picker.decimal);
  return selected;
}

function selectPiece(part, value) {
  model.picker[part] = value;
  model.picker.changed = true;
  model.draft++;
  clearSavedReceipt();
  paintPicker();
}

function shiftPicker(offset) {
  model.picker.anchor = Math.max(0, Math.min(1000, model.picker.anchor + offset));
  model.picker.shifted = true;
  if (!wholeWeightTiles(model.picker.anchor).includes(model.picker.whole)) model.picker.whole = null;
  model.draft++; clearSavedReceipt(); paintPicker();
}

function tileRow(group, part, values) {
  group.replaceChildren();
  for (const value of values) {
    const button = node("button", "weight-tile", part === "decimal" ? `.${value}` : String(value));
    button.type = "button"; button.dataset.value = value;
    button.setAttribute("aria-label", part === "decimal" ? `${value} tenths` : `${value} whole kilograms`);
    button.setAttribute("aria-pressed", "false");
    button.onclick = () => selectPiece(part, value);
    button.onkeydown = (event) => {
      const available = [...group.querySelectorAll("button:not(:disabled)")];
      const index = available.indexOf(button);
      let target;
      if (event.key === "ArrowLeft") target = available[(index - 1 + available.length) % available.length];
      if (event.key === "ArrowRight") target = available[(index + 1) % available.length];
      if (event.key === "Home") target = available[0];
      if (event.key === "End") target = available.at(-1);
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        const other = field(part === "whole" ? "weight-decimal" : "weight-whole");
        target = other.querySelector('[aria-pressed="true"]:not(:disabled)') || other.querySelector("button:not(:disabled)");
        if (target) { event.preventDefault(); target.focus({ preventScroll: true }); }
        return;
      }
      if (target) { event.preventDefault(); target.click(); target.focus({ preventScroll: true }); }
    };
    group.append(button);
  }
}

function paintPicker() {
  host.dataset.density = preferences.density; host.dataset.contrast = preferences.contrast;
  const picker = model.picker;
  const selecting = picker.whole !== null || picker.decimal !== null;
  if (picker.anchor === null || (!selecting && !picker.shifted && !model.editing)) picker.anchor = weightTileAnchor(model.rows);
  const whole = field("weight-whole"), decimal = field("weight-decimal");
  if (whole.dataset.anchor !== String(picker.anchor)) {
    tileRow(whole, "whole", wholeWeightTiles(picker.anchor)); whole.dataset.anchor = picker.anchor;
  }
  if (!decimal.children.length) tileRow(decimal, "decimal", Array.from({ length: 10 }, (_, index) => index));
  for (const [part, group] of [["whole", whole], ["decimal", decimal]]) {
    const buttons = [...group.querySelectorAll("button")];
    for (const button of buttons) {
      const value = Number(button.dataset.value), selected = picker[part] === value;
      button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
      button.disabled = part === "whole" ? value < 0 || value > 1000 : picker.whole === 1000 && value !== 0;
      button.tabIndex = -1;
    }
    const selected = buttons.find((button) => button.getAttribute("aria-pressed") === "true" && !button.disabled);
    (selected || buttons.find((button) => !button.disabled)).tabIndex = 0;
  }
  const value = chosenWeight();
  const exact = value !== null && Math.abs(value * 10 - Math.round(value * 10)) > 1e-8;
  field("weight-value").textContent = value !== null ? `${exact ? String(value) : value.toFixed(1)} kg` :
    `${picker.whole ?? "—"}.${picker.decimal ?? "—"} kg`;
  field("weight-picker-hint").textContent = exact ? "Original precision kept. Choose a tile to change the weight." :
    picker.whole === null && picker.decimal === null ? "" :
    picker.whole === null ? "Choose the whole kilograms." : picker.decimal === null ? "Choose the decimal, including .0." :
    value === null ? "Choose a weight between 0 and 1000 kg." : "";
  field("weight-save").disabled = value === null || Boolean(model.saving || model.deleting);
  field("weight-delete").disabled = !model.editing || Boolean(model.saving || model.deleting);
  field("weight-lower").disabled = picker.anchor <= 0;
  field("weight-higher").disabled = picker.anchor >= 1000;
  const day = field("weight-date").value;
  field("weight-entry-day").textContent = model.editing ? `Editing · ${formatDay(day)}` : day && day !== localDate() ? formatDay(day) : "";
  field("weight-entry-controls").querySelector("summary").setAttribute("aria-label", `Entry controls, ${model.editing ? "editing " : ""}${day === localDate() ? "today, " : ""}${day ? formatDay(day) : "choose a date"}`);
}

async function save(event) {
  event.preventDefault();
  if (model.saving || model.deleting) return;
  const value = chosenWeight(), day = field("weight-date").value;
  if (value === null || !/^\d{4}-\d{2}-\d{2}$/.test(day)) { status("Choose both weight tiles and a valid date.", true); return; }
  const previous = model.rows;
  const original = model.rows.find((row) => row.uid === model.editing);
  if (manualDateCollision(model.rows, original, day)) {
    status("This date already has a weighing; open that entry to update it.", true); return;
  }
  const measuredAt = dailyWeightMoment(day, original);
  const params = { measured_at: measuredAt, weight_kg: value, ...(model.editing ? { weight_uid: model.editing } : { uid: uuid() }) };
  const entry = { op: "upsert_weight", params };
  const epoch = model.epoch, token = model.token, draft = model.draft, marker = {};
  model.saving = marker; model.mutation++;
  field("weight-save").disabled = true;
  field("weight-delete").disabled = true;
  model.rows = patch(model.rows, entry); model.version++; paint();
  try {
    const outcome = await send(entry.op, params);
    if (!current(epoch, token)) return;
    const changedDraft = draft !== model.draft;
    const receipt = changedDraft ? "Previous weighing saved" : "Saved";
    const draftNotice = changedDraft && unsavedSelection() ? " Current selection is unsaved." : "";
    if (outcome.error) status(`${receipt} on this phone; sync needs attention: ${outcome.error}${draftNotice}`, true);
    else status(outcome.queued ? `${receipt} on this phone — will sync when connected.${draftNotice}` : `${receipt}.${draftNotice}`);
    const skipped = !outcome.queued && outcome.result?.skipped;
    if (skipped) {
      model.rows = model.rows.filter((r) => r.uid !== params.weight_uid);
      if (model.editing === params.weight_uid) {
        model.editing = null; field("weight-edit-actions").classList.add("hidden"); field("weight-save").textContent = "Save";
      }
      status("This weighing was removed on another device. Your selection is kept; Save again to add it.", true);
    } else if (!outcome.queued && outcome.result?.measurement) {
      const saved = outcome.result.measurement;
      model.rows = model.rows.filter((r) => r.uid !== params.uid && r.uid !== params.weight_uid && r.uid !== saved.uid);
      model.rows.push(saved); model.rows.sort((a, b) => a.measured_at.localeCompare(b.measured_at));
    }
    cache(); model.version++; model.mutation++;
    if (draft === model.draft && !skipped) {
      model.cleanDraft = model.draft;
      model.editing = null; model.automaticDate = true; resetPicker();
      field("weight-date").value = localDate(); field("weight-entry-controls").open = false;
      field("weight-edit-actions").classList.add("hidden");
      field("weight-save").textContent = "Save";
    }
    paint();
    if (!outcome.queued) { model.loaded = false; loadWeight(); }
  } catch (err) {
    if (!current(epoch, token)) return;
    model.rows = previous; model.version++; model.mutation++; paint(); status(err.message || "Weight could not be saved.", true);
  } finally {
    if (model.saving === marker) model.saving = null;
    if (current(epoch, token)) paintPicker();
  }
}

function cancelEdit() {
  model.draft++;
  model.cleanDraft = model.draft; model.automaticDate = true;
  clearSavedReceipt();
  model.editing = null; resetPicker(); field("weight-date").value = localDate();
  field("weight-edit-actions").classList.add("hidden"); field("weight-save").textContent = "Save";
  paintPicker();
}
async function deleteEntry() {
  if (model.saving || model.deleting) return;
  const uid = model.editing;
  if (!uid || !confirm("Delete this weighing?")) return;
  const previous = model.rows;
  const epoch = model.epoch, token = model.token, draft = model.draft, marker = {};
  model.deleting = marker; model.mutation++;
  field("weight-delete").disabled = true;
  field("weight-save").disabled = true;
  model.rows = model.rows.filter((r) => r.uid !== uid); model.version++; paint();
  try {
    const outcome = await send("delete_weight", { weight_uid: uid });
    if (!current(epoch, token)) return;
    cache(); model.mutation++;
    if (draft === model.draft) cancelEdit();
    status(outcome.error ? `Deletion saved on this phone; sync needs attention: ${outcome.error}` :
      outcome.queued ? "Deleted on this phone — will sync when connected." : "Deleted.", Boolean(outcome.error));
    if (!outcome.queued) { model.loaded = false; loadWeight(); }
  } catch (err) {
    if (!current(epoch, token)) return;
    model.rows = previous; model.version++; model.mutation++; paint(); status(err.message, true);
  } finally {
    if (model.deleting === marker) model.deleting = null;
    if (current(epoch, token)) paintPicker();
  }
}

function paint() {
  paintPicker();
  const newest = model.rows.at(-1), summary = field("weight-summary");
  summary.replaceChildren();
  if (newest) {
    const latest = node("div"); latest.append(node("div", "muted", "Latest"), node("strong", "weight-metric", `${Number(newest.weight_kg).toFixed(1)} kg`), node("div", "muted", formatDay(newest.measured_at)));
    summary.append(latest);
    const old = model.rows.findLast((r) => r.measured_at.slice(0, 10) <= new Date(new Date(newest.measured_at).getTime() - 30 * 86400000).toISOString().slice(0, 10));
    if (old) {
      const delta = Number(newest.weight_kg) - Number(old.weight_kg), change = node("div");
      change.append(node("div", "muted", "Change since"), node("strong", "weight-metric", `${delta > 0 ? "+" : ""}${delta.toFixed(1)} kg`), node("div", "muted", formatDay(old.measured_at)));
      summary.append(change);
    }
  }
  paintHistory(); paintChart();
}

function paintHistory() {
  const list = field("weight-history"); list.replaceChildren();
  for (const row of [...model.rows].reverse().slice(0, model.limit)) {
    const button = node("button", "card weight-history-row"); button.type = "button";
    const kind = row.source === "scale" ? "Scale" : row.source === "historic" ? "Historic" : "Manual";
    const time = row.source === "manual" ? "" : ` · ${row.measured_at.slice(11, 16)}`;
    const detail = node("div", "grow"); detail.append(node("div", "name", formatDay(row.measured_at)), node("div", "sub", `${row.pending ? "Waiting to sync" : kind}${time}`));
    button.append(detail, node("strong", "", `${Number(row.weight_kg).toFixed(1)} kg`));
    button.onclick = () => {
      model.draft++;
      clearSavedReceipt();
      model.editing = row.uid;
      model.automaticDate = false;
      model.picker = { anchor: Math.round(Number(row.weight_kg)), ...splitWeightTiles(row.weight_kg),
        original: parseWeight(row.weight_kg), changed: false, shifted: false };
      field("weight-date").value = row.measured_at.slice(0, 10);
      field("weight-entry-controls").open = true;
      field("weight-save").textContent = "Update"; field("weight-edit-actions").classList.remove("hidden");
      paintPicker(); field("weight-entry").scrollIntoView({ block: "start" });
      field("weight-whole").querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
    };
    list.append(button);
  }
  if (!model.rows.length) list.append(node("p", "muted", "Your weighings will appear here."));
  field("weight-more").classList.toggle("hidden", model.rows.length <= model.limit);
}

function paintChart() {
  field("weight-custom").classList.toggle("hidden", preferences.range !== "custom");
  field("weight-span").disabled = preferences.trend !== "loess";
  if (drawnVersion === model.version) return;
  const canvas = field("weight-canvas"), width = canvas.clientWidth;
  if (!width) return;
  drawnVersion = model.version;
  let start = "", end = "";
  if (preferences.range === "custom") { start = preferences.start; end = preferences.end; }
  else if (preferences.range !== "all") {
    const day = new Date(`${localDate()}T12:00:00`); day.setDate(day.getDate() - Number(preferences.range) + 1);
    start = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  }
  const rows = selectMeasurements(model.rows, { start, end, historic: preferences.historic });
  const points = pointsFor(rows);
  const trend = trendFor(points, preferences.trend, Number(preferences.span));
  const height = Math.max(120, Math.round(canvas.clientHeight)), ratio = Math.min(2, devicePixelRatio || 1);
  canvas.width = Math.round(width * ratio); canvas.height = height * ratio;
  const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, width, height);
  ctx.font = "12px -apple-system, sans-serif";
  if (!points.length) {
    ctx.fillStyle = "#555"; ctx.textAlign = "center";
    ctx.fillText(start && end && start > end ? "Choose an end date after the start date." : "No weighings in this date range", width / 2, height / 2);
    canvas.setAttribute("aria-label", "No weight measurements in the selected date range");
    canvas.onpointerdown = null; return;
  }
  const left = 48, right = width - 15, top = 18, bottom = height - 42;
  const first = points[0].x, last = points.at(-1).x;
  const x0 = first === last ? first - 86400000 : first, x1 = first === last ? last + 86400000 : last;
  const values = [...points, ...trend].map((p) => p.y);
  let min = Math.min(...values), max = Math.max(...values);
  const step = max - min > 25 ? 5 : max - min > 10 ? 2 : 1;
  min = preferences.zero ? 0 : Math.floor((min - 0.5) / step) * step;
  max = Math.ceil((max + 0.5) / step) * step;
  if (max <= min) max = min + 1;
  const px = (x) => left + (x - x0) / (x1 - x0) * (right - left), py = (y) => bottom - (y - min) / (max - min) * (bottom - top);
  ctx.fillStyle = "#ebebeb"; ctx.fillRect(left, top, right - left, bottom - top);
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  const tick = preferences.zero ? Math.max(step, Math.ceil((max - min) / 8 / 5) * 5) : step;
  for (let value = min; value <= max + 1e-8; value += tick) {
    const y = py(value); ctx.strokeStyle = "#fff"; ctx.lineWidth = value % 5 === 0 ? 1.8 : 0.8;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillStyle = "#4b4b4b"; ctx.fillText(String(Math.round(value * 10) / 10), left - 8, y);
  }
  ctx.fillStyle = "#555"; ctx.textAlign = "left"; ctx.fillText("kg", 8, 12);
  const labels = width < 450 ? 3 : 5;
  for (let i = 0; i < labels; i++) {
    const x = x0 + (x1 - x0) * i / (labels - 1);
    ctx.textAlign = i === 0 ? "left" : i === labels - 1 ? "right" : "center";
    const date = new Date(x);
    ctx.fillText(date.toLocaleDateString(undefined, { month: "short", day: "numeric" }), px(x), bottom + 18);
    if (x1 - x0 > 365 * 86400000) ctx.fillText(String(date.getFullYear()), px(x), bottom + 33);
  }
  ctx.save(); ctx.beginPath(); ctx.rect(left - 3, top - 3, right - left + 6, bottom - top + 6); ctx.clip();
  if (preferences.points) {
    ctx.fillStyle = "#888"; ctx.beginPath();
    for (const point of points) { ctx.moveTo(px(point.x) + 2.1, py(point.y)); ctx.arc(px(point.x), py(point.y), 2.1, 0, Math.PI * 2); }
    ctx.fill();
  }
  if (trend.length) {
    ctx.strokeStyle = "#111"; ctx.lineWidth = 2.2; ctx.beginPath();
    trend.forEach((point, index) => index ? ctx.lineTo(px(point.x), py(point.y)) : ctx.moveTo(px(point.x), py(point.y))); ctx.stroke();
  }
  ctx.restore();
  canvas.setAttribute("aria-label", `${points.length} weight measurements from ${formatDay(rows[0].measured_at)} to ${formatDay(rows.at(-1).measured_at)}, latest ${points.at(-1).y} kilograms`);
  canvas.onpointerdown = (event) => {
    const box = canvas.getBoundingClientRect(), at = x0 + (event.clientX - box.left - left) / (right - left) * (x1 - x0);
    const closest = points.reduce((a, b) => Math.abs(a.x - at) <= Math.abs(b.x - at) ? a : b);
    field("weight-chart-detail").textContent = `${formatDay(closest.row.measured_at)} · ${closest.y.toFixed(2)} kg`;
  };
}
