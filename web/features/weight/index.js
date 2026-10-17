import { el as node } from "../../shared/dom.js";
import { localDate } from "../../shared/time.js";
import { STORAGE_KEYS, accountTag } from "../../shared/storage.js";
import { createFullscreenController } from "../../shared/fullscreen.js";
import { drawWeightChart, WEIGHT_CHART_PALETTES } from "./chart.js";
import { projectWeightOperation, findWeightMeasurement, withWeightAliases } from "./projection.js";
import { parseWeight, selectMeasurements, pointsFor, trendFor, trendBetween, weightTimestamp, weightDayOffset } from "./math.js";
import { wholeWeightTiles, weightTileAnchor, splitWeightTiles, selectedTileWeight, dailyWeightMoment, manualDateCollision } from "./picker.js";

export function createWeightFeature({ api, send, uuid, getToken, pendingOperations }) {
  const CACHE = STORAGE_KEYS.weightCache, PREFS = STORAGE_KEYS.weightPreferences;
  const model = { rows: [], aliases: [], today: "", loaded: false, loading: null, token: null, editing: null,
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
  for (const key of ["start", "end"]) if (!/^\d{4}-\d{2}-\d{2}$/.test(preferences[key]) || weightTimestamp(preferences[key]) === null) preferences[key] = "";
  if (!["comfortable", "compact"].includes(preferences.density)) preferences.density = "comfortable";
  if (!["normal", "high"].includes(preferences.contrast)) preferences.contrast = "normal";
  let host, chartObserver, fullscreen, calendarInterval = null, drawnVersion = -1, chartGeometry = null, inspectedUid = null;
  let chartData = null, trendData = null, zoom = null;
  const MIN_ZOOM_SPAN = 7 * 86400000;

  const field = (id) => host.querySelector(`#${id}`);
  const formatDay = (value) => {
    const timestamp = weightTimestamp(value.slice(0, 10));
    return timestamp === null ? "Choose a date" : new Date(timestamp).toLocaleDateString(undefined,
      { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  };
  const historyRows = (rows) => selectMeasurements(rows, { historic: true })
    .filter((row) => typeof row.uid === "string" && row.uid)
    .map((row) => row.weight_kg === parseWeight(row.weight_kg) ? row : { ...row, weight_kg: parseWeight(row.weight_kg) });

  function status(text, error = false) {
    const output = field("weight-status");
    if (output.textContent !== text) output.textContent = text;
    output.classList.toggle("error", error);
  }
  function clearSavedReceipt() {
    const output = field("weight-status");
    if (!output.classList.contains("error") && /^Saved\.$|^Previous weighing saved\.(?: Current selection is unsaved\.)?$/.test(output.textContent)) status("");
  }
  const savedReceipt = () => unsavedSelection() ? "Previous weighing saved. Current selection is unsaved." : "Saved.";
  function cache() {
    try { localStorage.setItem(CACHE, JSON.stringify({ owner: accountTag(model.token), rows: model.rows, aliases: model.aliases, today: model.today })); } catch { /* send() verifies durable write storage */ }
  }
  const current = (epoch, token) => Boolean(host && model.epoch === epoch && model.token === token && getToken() === token);
  function overlay(rows) {
    return pendingOperations().reduce(projectWeightOperation, withWeightAliases(rows, model.aliases));
  }

  function invalidateWeight() { model.loaded = false; }
  function resetWeight() {
    model.rows = []; model.aliases = []; model.loaded = false; model.today = ""; model.token = null;
    model.editing = null; model.loading = null; model.saving = null; model.deleting = null;
    model.version++; model.epoch++; model.draft++;
    model.calendar = ""; model.automaticDate = true; model.cleanDraft = model.draft;
    resetPicker();
    chartGeometry = null; inspectedUid = null; chartData = null; trendData = null; zoom = null;
    try { localStorage.removeItem(CACHE); } catch { /* ignore */ }
    fullscreen?.destroy(); fullscreen = null;
    host = null; chartObserver?.disconnect();
  }

  function renderWeight(container) {
    mount();
    const token = getToken();
    if (model.token !== token) {
      model.rows = []; model.aliases = []; model.loaded = false; model.editing = null; model.loading = null;
      model.today = ""; model.limit = 12; model.saving = null; model.deleting = null;
      model.token = token;
      model.epoch++; model.draft++;
      model.calendar = ""; model.automaticDate = true; model.cleanDraft = model.draft;
      resetPicker();
      chartGeometry = null; inspectedUid = null; chartData = null; trendData = null; zoom = null;
      fullscreen?.destroy(); fullscreen = null;
      host = null; chartObserver?.disconnect();
      try {
        const stored = JSON.parse(localStorage.getItem(CACHE) || "null");
        if (stored?.owner === accountTag(token) && Array.isArray(stored?.rows)) {
          model.rows = historyRows(stored.rows);
          if (Array.isArray(stored.aliases)) model.aliases = stored.aliases;
          model.rows = overlay(model.rows);
        }
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
        if (Array.isArray(data.aliases)) model.aliases = data.aliases;
        model.rows = overlay(historyRows(data.measurements));
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
          <button id="weight-clear" class="ghost" type="button" aria-label="Clear weight selection" title="Clear weight selection">↻</button>
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
        </select><button id="weight-options-toggle" class="ghost weight-tool" type="button" aria-label="Plot options" aria-expanded="false" aria-controls="weight-plot-options"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/></svg></button><button id="weight-zoom-reset" class="ghost hidden" type="button">Reset zoom</button><button id="weight-fullscreen" class="ghost" type="button" aria-label="Open weight plot fullscreen" aria-pressed="false" aria-expanded="false">⛶</button></div>
        <div id="weight-plot-options" class="weight-options weight-plot-options hidden">
          <label>Trend<select id="weight-trend"><option value="loess">LOESS (original plot)</option><option value="mean7">7-day average</option><option value="none">No trend</option></select></label>
          <label>Smoothing<select id="weight-span"><option value="0.04">Original (4%)</option><option value="0.08">Smoother (8%)</option><option value="0.15">Broad (15%)</option></select></label>
          <label class="weight-check"><input id="weight-points" type="checkbox">Show measurements</label>
          <label class="weight-check"><input id="weight-historic" type="checkbox">Include historic measurements</label>
          <label class="weight-check"><input id="weight-zero" type="checkbox">Start weight axis at zero</label>
          <p class="muted">The trend is fitted over your whole history, so it keeps its shape for every range. The historic curve interpolates between sparse measurements.</p>
          <div class="weight-export"><button id="weight-png" type="button" class="ghost">Save plot</button><button id="weight-csv" type="button" class="ghost">Export data</button></div>
        </div>
        <div id="weight-custom" class="weight-custom hidden">
          <label>From<input id="weight-start" type="date"></label><label>To<input id="weight-end" type="date"></label>
        </div>
        <canvas id="weight-canvas" role="img" tabindex="0" aria-label="Body weight over time" aria-describedby="weight-chart-help"></canvas>
        <p id="weight-chart-help" class="muted">Tap the plot or use Left and Right arrows to inspect a weighing. In fullscreen, pinch to zoom and drag to move.</p>
        <p id="weight-chart-detail" class="muted" aria-live="polite" aria-atomic="true"></p>
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
    field("weight-options-toggle").onclick = () => {
      const panel = field("weight-plot-options"), open = panel.classList.toggle("hidden") === false;
      field("weight-options-toggle").setAttribute("aria-expanded", String(open));
    };
    field("weight-zoom-reset").onclick = () => setZoom(null);
    field("weight-more").onclick = () => { model.limit += 30; paintHistory(); };
    for (const key of ["range", "trend", "span", "points", "historic", "zero", "start", "end", "density", "contrast"]) {
      const input = field(`weight-${key}`);
      if (input.type === "checkbox") input.checked = Boolean(preferences[key]);
      else input.value = preferences[key];
      input.addEventListener("change", () => {
        preferences[key] = input.type === "checkbox" ? input.checked : input.value;
        try { localStorage.setItem(PREFS, JSON.stringify(preferences)); } catch { /* optional preferences */ }
        if (key === "density" || key === "contrast") paintPicker();
        else { if (["range", "start", "end"].includes(key)) zoom = null; drawnVersion = -1; paintChart(); }
      });
    }
    // The screen chart follows the dark app theme; saved plots keep the light figure.
    field("weight-png").onclick = () => {
      const screen = field("weight-canvas"), figure = document.createElement("canvas");
      const { points, trend, invalid } = chartView();
      drawWeightChart(figure, points, trend, preferences, invalid, {
        palette: WEIGHT_CHART_PALETTES.print, width: screen.clientWidth || 640, height: screen.clientHeight || 360, ratio: 2,
      });
      figure.toBlob((blob) => { if (blob) download(blob, "weight-plot.png"); });
    };
    field("weight-csv").onclick = () => {
      const rows = ["measured_at,weight_kg,source", ...model.rows.map((r) => `${r.measured_at},${Number(r.weight_kg)},${r.source}`)];
      download(new Blob([rows.join("\r\n")], { type: "text/csv;charset=utf-8" }), "weight-data.csv");
    };
    fullscreen = createFullscreenController(field("weight-plot"), { label: "Weight over time", onChange: (open) => {
      if (!host) return;
      const button = field("weight-fullscreen");
      button.textContent = open ? "×" : "⛶";
      button.setAttribute("aria-label", open ? "Close weight plot fullscreen" : "Open weight plot fullscreen");
      button.setAttribute("aria-pressed", String(open));
      button.setAttribute("aria-expanded", String(open));
      if (!open) zoom = null;
      drawnVersion = -1;
      requestAnimationFrame(() => { if (host) paintChart(); });
    } });
    field("weight-fullscreen").onclick = () => fullscreen.toggle();
    chartObserver = new ResizeObserver(() => { drawnVersion = -1; if (host && !host.classList.contains("hidden")) paintChart(); });
    chartObserver.observe(field("weight-canvas"));
    bindChartGestures(field("weight-canvas"));
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
  function onCalendarTick() { if (!document.hidden) checkCalendarDay(); }
  function mount() {
    if (calendarInterval !== null) return;
    document.addEventListener("visibilitychange", onCalendarTick);
    window.addEventListener("pageshow", checkCalendarDay);
    calendarInterval = setInterval(onCalendarTick, 60000);
  }
  function destroy() {
    resetWeight();
    if (calendarInterval === null) return;
    clearInterval(calendarInterval); calendarInterval = null;
    document.removeEventListener("visibilitychange", onCalendarTick);
    window.removeEventListener("pageshow", checkCalendarDay);
  }

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
      const focusedValue = whole.contains(document.activeElement) ? document.activeElement.dataset.value : null;
      tileRow(whole, "whole", wholeWeightTiles(picker.anchor)); whole.dataset.anchor = picker.anchor;
      if (focusedValue !== null) {
        ([...whole.querySelectorAll("button")].find((button) => button.dataset.value === focusedValue) || whole.querySelector("button:not(:disabled)"))?.focus({ preventScroll: true });
      }
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
    const display = value !== null ? `${exact ? String(value) : value.toFixed(1)} kg` : `${picker.whole ?? "—"}.${picker.decimal ?? "—"} kg`;
    if (field("weight-value").textContent !== display) field("weight-value").textContent = display;
    const hint = exact ? "Original precision kept. Choose a tile to change the weight." :
      picker.whole === null && picker.decimal === null ? "" :
      picker.whole === null ? "Choose the whole kilograms." : picker.decimal === null ? "Choose the decimal, including .0." :
      value === null ? "Choose a weight between 0 and 1000 kg." : "";
    if (field("weight-picker-hint").textContent !== hint) field("weight-picker-hint").textContent = hint;
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
    if (value === null || !/^\d{4}-\d{2}-\d{2}$/.test(day) || weightTimestamp(day) === null) { status("Choose both weight tiles and a valid date.", true); return; }
    const previous = model.rows;
    const original = findWeightMeasurement(model.rows, model.editing);
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
    model.rows = projectWeightOperation(model.rows, entry); model.version++; paint();
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
        const removed = findWeightMeasurement(model.rows, params.weight_uid);
        model.rows = model.rows.filter((row) => row !== removed);
        if (model.editing === params.weight_uid) {
          model.editing = null; field("weight-edit-actions").classList.add("hidden"); field("weight-save").textContent = "Save";
        }
        status("This weighing was removed on another device. Your selection is kept; Save again to add it.", true);
      } else if (!outcome.queued && outcome.result?.measurement) {
        const saved = outcome.result.measurement;
        const previousRow = findWeightMeasurement(model.rows, params.weight_uid || params.uid);
        const aliases = [...new Set([params.uid, params.weight_uid, previousRow?.uid,
          ...(Array.isArray(previousRow?.pending_uids) ? previousRow.pending_uids : [])]
          .filter((uid) => uid && uid !== saved.uid))];
        model.aliases = [...model.aliases.filter((alias) => !aliases.includes(alias.alias_uid)),
          ...aliases.map((uid) => ({ alias_uid: uid, weight_uid: saved.uid }))];
        model.rows = model.rows.filter((row) => row !== previousRow && row.uid !== saved.uid);
        model.rows.push(saved); model.rows = overlay(model.rows);
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
    model.rows = projectWeightOperation(model.rows, { op: "delete_weight", params: { weight_uid: uid } }); model.version++; paint();
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
      const cutoff = weightDayOffset(newest.measured_at.slice(0, 10), -30);
      const old = cutoff && model.rows.findLast((r) => r.measured_at.slice(0, 10) <= cutoff);
      if (old) {
        const delta = Number(newest.weight_kg) - Number(old.weight_kg), change = node("div");
        change.append(node("div", "muted", "Change since"), node("strong", "weight-metric", `${delta > 0 ? "+" : ""}${delta.toFixed(1)} kg`), node("div", "muted", formatDay(old.measured_at)));
        summary.append(change);
      }
    }
    paintHistory(); paintChart();
  }

  function paintHistory() {
    const focusedUid = field("weight-history").contains(document.activeElement) ? document.activeElement.dataset.weightUid : null;
    const list = field("weight-history"); list.replaceChildren();
    for (const row of [...model.rows].reverse().slice(0, model.limit)) {
      const button = node("button", "card weight-history-row"); button.type = "button";
      button.dataset.weightUid = row.uid;
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
    if (focusedUid) [...list.querySelectorAll("button")].find((button) => button.dataset.weightUid === focusedUid)?.focus({ preventScroll: true });
  }

  // Fit the trend once over the whole history and show its visible stretch, so a
  // smoothing preset means the same neighbourhood at every range and zoom.
  function fittedTrend() {
    const key = JSON.stringify([model.version, preferences.historic, preferences.trend, preferences.span]);
    if (trendData?.key !== key) {
      const points = pointsFor(selectMeasurements(model.rows, { historic: preferences.historic }));
      trendData = { key, points, trend: trendFor(points, preferences.trend, Number(preferences.span)) };
    }
    return trendData;
  }

  function chartView() {
    let start = "", end = "";
    if (preferences.range === "custom") { start = preferences.start; end = preferences.end; }
    else if (preferences.range !== "all") {
      end = localDate();
      start = weightDayOffset(end, -Number(preferences.range) + 1);
    }
    const fit = fittedTrend();
    const key = JSON.stringify([fit.key, start, end]);
    if (chartData?.key !== key) {
      const rows = selectMeasurements(model.rows, { start, end, historic: preferences.historic });
      chartData = { key, points: pointsFor(rows), invalid: Boolean(start && end && start > end) };
    }
    // A fullscreen zoom may pan beyond the chosen range across the whole history.
    const domain = zoom && fullscreen?.isOpen ? zoom : null;
    const points = domain ? fit.points.filter((point) => point.x >= domain.x0 && point.x <= domain.x1) : chartData.points;
    const x0 = domain ? domain.x0 : points[0]?.x, x1 = domain ? domain.x1 : points.at(-1)?.x;
    return { points, trend: trendBetween(fit.trend, x0, x1), domain, invalid: chartData.invalid };
  }

  function setZoom(next) {
    zoom = next;
    drawnVersion = -1;
    paintChart();
  }

  function clampZoom(x0, span) {
    const points = fittedTrend().points;
    if (!points.length) return null;
    const first = points[0].x, last = points.at(-1).x;
    const width = Math.max(MIN_ZOOM_SPAN, Math.min(Math.max(MIN_ZOOM_SPAN, last - first), span));
    const left = Math.max(first, Math.min(last - width, x0));
    return { x0: left, x1: left + width };
  }

  function zoomBy(factor) {
    if (!chartGeometry) return;
    const { x0, x1 } = chartGeometry, middle = (x0 + x1) / 2, span = (x1 - x0) * factor;
    setZoom(clampZoom(middle - span / 2, span));
  }

  function inspect(point) {
    inspectedUid = point.row.uid;
    const message = `${formatDay(point.row.measured_at)} · ${point.y} kg`;
    if (field("weight-chart-detail").textContent !== message) field("weight-chart-detail").textContent = message;
  }

  function inspectAt(clientX) {
    if (!chartGeometry?.points.length) return;
    const { x0, x1, left, right, points } = chartGeometry, box = field("weight-canvas").getBoundingClientRect();
    const at = x0 + (clientX - box.left - left) / (right - left) * (x1 - x0);
    inspect(points.reduce((a, b) => Math.abs(a.x - at) <= Math.abs(b.x - at) ? a : b));
  }

  // Inline, a tap inspects the nearest weighing and the page still scrolls.
  // Fullscreen adds iPhone-style gestures: pinch to zoom the time axis, drag to
  // pan, double-tap to reset.
  function bindChartGestures(canvas) {
    const pointers = new Map();
    let start = null, moved = false, lastTap = { time: 0, x: 0 }, frame = null, pending = null;
    const flush = () => {
      if (frame !== null) { cancelAnimationFrame(frame); frame = null; }
      if (pending) { const next = pending; pending = null; setZoom(next); }
    };
    const schedule = (next) => {
      pending = next;
      if (frame === null) frame = requestAnimationFrame(() => { frame = null; flush(); });
    };
    const spread = (list) => Math.max(24, Math.hypot(list[0].x - list[1].x, list[0].y - list[1].y));
    function begin() {
      flush();
      const list = [...pointers.values()];
      if (!chartGeometry || !list.length) { start = null; return; }
      const box = canvas.getBoundingClientRect(), { x0, x1, left, right } = chartGeometry;
      start = { x0, span: x1 - x0, left: box.left + left, width: right - left,
        mid: list.reduce((sum, point) => sum + point.x, 0) / list.length,
        distance: list.length > 1 ? spread(list) : 0 };
    }
    canvas.addEventListener("pointerdown", (event) => {
      if (!fullscreen?.isOpen) { inspectAt(event.clientX); return; }
      try { canvas.setPointerCapture(event.pointerId); } catch { /* an ended or synthetic pointer */ }
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY });
      moved = pointers.size > 1;
      begin();
    });
    canvas.addEventListener("pointermove", (event) => {
      const pointer = pointers.get(event.pointerId);
      if (!pointer || !start) return;
      pointer.x = event.clientX; pointer.y = event.clientY;
      if (!moved && Math.hypot(pointer.x - pointer.startX, pointer.y - pointer.startY) < 6) return;
      moved = true;
      const list = [...pointers.values()];
      const mid = list.reduce((sum, point) => sum + point.x, 0) / list.length;
      const span = list.length > 1 && start.distance ? start.span * start.distance / spread(list) : start.span;
      // Keep the date under the fingers' starting midpoint beneath their midpoint.
      const anchor = start.x0 + (start.mid - start.left) / start.width * start.span;
      const next = clampZoom(anchor - (mid - start.left) / start.width * span, span);
      if (next) schedule(next);
    });
    const finish = (event) => {
      const pointer = pointers.get(event.pointerId);
      if (!pointer) return;
      pointers.delete(event.pointerId);
      if (pointers.size) { begin(); return; }
      flush(); start = null;
      if (event.type !== "pointerup" || moved) return;
      const now = performance.now();
      if (now - lastTap.time < 320 && Math.abs(pointer.x - lastTap.x) < 30) { lastTap = { time: 0, x: 0 }; setZoom(null); }
      else { lastTap = { time: now, x: pointer.x }; inspectAt(pointer.x); }
    };
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", finish);
    // Safari's page-zoom gesture must not take over the fullscreen plot.
    canvas.addEventListener("gesturestart", (event) => { if (fullscreen?.isOpen) event.preventDefault(); });
  }

  function paintChart() {
    field("weight-custom").classList.toggle("hidden", preferences.range !== "custom");
    field("weight-span").disabled = preferences.trend !== "loess";
    field("weight-zoom-reset").classList.toggle("hidden", !(zoom && fullscreen?.isOpen));
    if (drawnVersion === model.version) return;
    const canvas = field("weight-canvas");
    if (!canvas.clientWidth) return;
    drawnVersion = model.version;
    const view = chartView();
    const geometry = drawWeightChart(canvas, view.points, view.trend, preferences, view.invalid, { domain: view.domain });
    chartGeometry = geometry ? { ...geometry, points: view.points } : null;
    const shown = view.points;
    if (!geometry || !shown.length) {
      canvas.setAttribute("aria-label", view.domain ? "No weighings in this zoomed range" : "No weight measurements in the selected date range");
      canvas.tabIndex = geometry ? 0 : -1; canvas.onkeydown = null; return;
    }
    canvas.setAttribute("aria-label", `${shown.length} weight measurements from ${formatDay(shown[0].row.measured_at)} to ${formatDay(shown.at(-1).row.measured_at)}, latest ${shown.at(-1).y} kilograms`);
    canvas.tabIndex = 0;
    canvas.onkeydown = (event) => {
      if (fullscreen?.isOpen && ["+", "=", "-", "0"].includes(event.key)) {
        event.preventDefault();
        if (event.key === "0") setZoom(null); else zoomBy(event.key === "-" ? 1.5 : 1 / 1.5);
        return;
      }
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let index = shown.findIndex((point) => point.row.uid === inspectedUid);
      if (index < 0) index = shown.length - 1;
      if (event.key === "Home") index = 0;
      else if (event.key === "End") index = shown.length - 1;
      else index = Math.max(0, Math.min(shown.length - 1, index + (event.key === "ArrowLeft" ? -1 : 1)));
      inspect(shown[index]);
    };
  }
  return { mount, render: renderWeight, invalidate: invalidateWeight, reset: resetWeight, destroy };
}
