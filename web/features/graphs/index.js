import { $, el, safeColor } from "../../shared/dom.js";
import { addDays, localIso } from "../../shared/time.js";
import { hm, prettyDayShort, shortLabel } from "../../shared/format.js";
import { openSheet, closeSheet, field } from "../../shared/sheet.js";
import { agendaNowMarker, agendaHourLabel, dayStartHour } from "./agenda-time.js";
import { createFullscreenController } from "../../shared/fullscreen.js";
import { STORAGE_KEYS } from "../../shared/storage.js";

export function createGraphsFeature({ store, api, banner, getView, navigateDay, editSession }) {
  const state = { graphMode: "bars", graphRange: "7", graphGroup: "auto", graphStart: "", graphEnd: "", graphHourStart: 6, graphHourEnd: 23, graphAutofit: true, graphs: null };
  const GRAPH_PREFS_KEY = STORAGE_KEYS.graphs;
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
  const graphCache = new Map();
  let graphRequest = 0, graphPaintKey = null, graphFullscreen = null, agendaLayout = null, graphResizeFrame = null, observer = null;
  const agendaScroll = new Map();

  function automaticGrouping(range) {
    if (range >= 365) return "monthly";
    if (range >= 30) return "weekly";
    return "daily";
  }

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
    if (graphCache.has(key) && !store.active) {
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
          const persisted = (store.snapshot?.sessions || []).find((row) => row.uid === session.uid);
          if (persisted?.end_time) editSession({ ...persisted, subject_name: session.subject_name });
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
      scrollKey, restoring: false, boundary: dayStartHour(data, store.context?.day_start) };
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
    if (!agendaLayout || getView() !== "graphs" || state.graphMode !== "agenda") return;
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
      if (getView() !== "graphs") return;
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

  async function graphChanged() {
    state.graphs = null;
    saveGraphPreferences();
    renderGraphs();
    await loadGraphs();
    if (getView() === "graphs") renderGraphs();
  }

  function mount() {
    document.querySelectorAll("#view-graphs .seg-btn").forEach((button) => {
      button.onclick = () => {
        if (button.dataset.mode) state.graphMode = button.dataset.mode;
        if (button.dataset.range) state.graphRange = button.dataset.range;
        if (button.dataset.group) state.graphGroup = button.dataset.group;
        if (state.graphRange === "custom" && !state.graphStart) {
          state.graphEnd = store.context?.today || localIso(new Date()).slice(0, 10);
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
    if (typeof ResizeObserver === "function") { observer = new ResizeObserver(queueGraphResize); observer.observe($("view-graphs")); }
  }
  function invalidate() { graphCache.clear(); }
  function reset() { graphRequest++; state.graphs = null; graphCache.clear(); agendaScroll.clear(); graphPaintKey = null; agendaLayout = null; }
  function destroy() { observer?.disconnect(); window.removeEventListener("resize", queueGraphResize); graphFullscreen?.destroy(); if (graphResizeFrame !== null) cancelAnimationFrame(graphResizeFrame); }
  return { mount, render: renderGraphs, load: loadGraphs, invalidate, reset, destroy, clock: updateAgendaNow, resize: queueGraphResize, get data() { return state.graphs; } };
}
