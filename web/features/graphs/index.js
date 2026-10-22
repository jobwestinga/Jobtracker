import { $, el, safeColor, captureFocus, restoreFocus } from "../../shared/dom.js";
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
  let graphRequest = 0, graphPaintKey = null, graphFullscreen = null, agendaLayout = null, graphResizeFrame = null, observer = null, mounted = false;
  const agendaScroll = new Map();
  const fullDay = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  function keyboardActivate(element) {
    element.setAttribute("role", "button"); element.tabIndex = 0;
    element.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); element.click(); }
    };
  }

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
    for (const [key, value] of [["mode", state.graphMode], ["range", state.graphRange]]) {
      document.querySelectorAll(`#view-graphs [data-${key}]`).forEach((button) => {
        const selected = button.dataset[key] === value;
        button.classList.toggle("on", selected); button.setAttribute("aria-pressed", String(selected));
      });
    }
    const host = $("graph-body");
    const paintKey = JSON.stringify([state.graphs, state.graphRange, state.graphHourStart, state.graphHourEnd, state.graphAutofit]);
    if (paintKey === graphPaintKey) {
      updateAgendaNow();
      queueGraphResize();
      return;
    }
    graphPaintKey = paintKey;
    const focusKey = captureFocus(host);
    agendaLayout = null;
    host.innerHTML = "";
    $("graph-legend").innerHTML = "";

    if (!state.graphs) {
      $("graph-total").textContent = "";
      host.append(el("div", "spinner", "Loading…"));
      restoreFocus(host, focusKey, $("graph-fullscreen"));
      return;
    }
    if (state.graphs.kind === "error") {
      $("graph-total").textContent = "";
      host.append(el("p", "muted", `Could not load graphs: ${state.graphs.message}`));
      restoreFocus(host, focusKey, $("graph-fullscreen"));
      return;
    }
    if (state.graphs.kind === "agenda") drawAgenda(host, state.graphs.data);
    else drawBars(host, state.graphs.data);
    restoreFocus(host, focusKey, $("graph-fullscreen"));
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
      column.dataset.focusKey = `bar:${data.grouping}:${bucket.date}`;
      const stack = el("div", "bar-stack");
      const fill = el("div", "bar-fill");
      fill.style.height = `${(bucket.total_seconds / peak) * 100}%`;
      stack.append(fill);
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
        piece.style.height = `${bucket.total_seconds ? (segment.seconds / bucket.total_seconds) * 100 : 0}%`;
        piece.style.backgroundColor = safeColor(segment.color);
        piece.title = `${segment.name}: ${hm(segment.seconds)}`;
        piece.setAttribute("aria-hidden", "true");
        fill.append(piece);
      }
      const value = el("div", "bar-val");
      if (bucket.total_seconds) {
        const hours = (bucket.intensity_seconds ?? bucket.total_seconds) / 3600;
        const tiers = [[1, "#94A3B8"], [2.5, "#86EFAC"], [4.5, "#60A5FA"],
          [7, "#A855F7"], [10, "#FB923C"], [Infinity, "#EF4444"]];
        const [, color] = tiers.find(([limit]) => hours <= limit);
        const scale = 0.85 + Math.min(Math.max(0, hours), 14) / 15;
        value.style.setProperty("--hour-color", color);
        value.style.setProperty("--hour-scale", scale);
        value.classList.toggle("bar-val--strong", hours > 4.5);
        value.classList.toggle("bar-val--peak", hours > 10);
        value.append(el("span", null, `${(bucket.total_seconds / 3600).toFixed(1)}h`));
      }
      const label = el("div", "bar-lbl", shortLabel(bucket.date, data.grouping));
      column.append(value, stack, label);
      // Tapping a day opens exactly that day, the way the desktop's charts do.
      if (data.grouping === "daily") {
        column.onclick = () => navigateDay(bucket.date);
        keyboardActivate(column);
      }
      column.setAttribute("aria-label", `${data.grouping === "daily" ? "Open sessions for " : ""}${fullDay(bucket.date)}, ${hm(bucket.total_seconds)} tracked${segments.length ? `: ${segments.map((segment) => `${segment.name} ${hm(segment.seconds)}`).join(", ")}` : ""}`);
      if (data.grouping !== "daily") column.setAttribute("role", "img");
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
    scroll.tabIndex = 0; scroll.setAttribute("role", "region");
    scroll.dataset.focusKey = "agenda-scroll";
    scroll.setAttribute("aria-label", "Session agenda. Scroll horizontally to see more days.");
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
      track.setAttribute("role", "group"); track.setAttribute("aria-label", `${fullDay(day)} sessions`);
      track.style.height = `${height}px`;
      tracks.set(day, track);

      for (const session of byDay.get(day)) {
        if (session.end_h <= lo || session.start_h >= hi) continue;
        const top = percentFor(Math.max(lo, session.start_h));
        const blockHeight = Math.max(0, percentFor(Math.min(hi, session.end_h)) - top);
        const block = el("div", "agenda-block");
        block.style.top = `${top}%`;
        block.style.height = `${blockHeight}%`;
        // Neutral dark base plus a strong subject tint and a solid left bar (CSS).
        block.style.setProperty("--subject", safeColor(session.color));
        if (blockHeight / 100 * height > 16) block.textContent = session.subject_name;
        block.title = `${session.subject_name} · ${hm(session.duration_seconds)}`;
        const startClock = session.start_time?.slice(11, 16) || agendaHourLabel(session.start_h);
        const endClock = session.end_time?.slice(11, 16) || agendaHourLabel(session.end_h);
        block.setAttribute("aria-label", `${session.subject_name}, ${fullDay(day)}, ${startClock} to ${endClock}, ${hm(session.duration_seconds)}. Open session.`);
        block.dataset.focusKey = `session:${session.uid}`;
        block.onclick = (event) => {
          event.stopPropagation();
          const persisted = (store.snapshot?.sessions || []).find((row) => row.uid === session.uid);
          if (persisted?.end_time) editSession({ ...persisted, subject_name: session.subject_name });
          else navigateDay(day);
        };
        keyboardActivate(block);
        track.append(block);
      }

      // Tapping a day opens it in Sessions, where it can actually be edited.
      track.onclick = () => navigateDay(day);

      const label = el("button", "agenda-daylbl", prettyDayShort(day));
      label.type = "button"; label.setAttribute("aria-label", `Open sessions for ${fullDay(day)}`);
      label.dataset.focusKey = `day:${day}`;
      label.onclick = () => navigateDay(day);
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
      const body = $("graph-body");
      const chart = agendaLayout?.wrap || body.querySelector(".bars");
      if (!chart) return;
      // Fullscreen is a fixed column: the chart fills the body below the
      // controls. The agenda also needs its padding, border and day labels.
      const available = body.clientHeight - (chart.getBoundingClientRect().top - body.getBoundingClientRect().top) - (agendaLayout ? 66 : 2);
      const height = fullscreen ? Math.max(100, available) : agendaLayout ? 300 : 260;
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
    openSheet("Graph options", (body) => {
      const group = document.createElement("select");
      for (const [value, label] of [["auto", "Automatic (follows range)"], ["daily", "Days"], ["weekly", "Weeks"], ["monthly", "Months"]]) {
        const option = el("option", null, label); option.value = value; option.selected = value === state.graphGroup;
        group.append(option);
      }
      field(body, "Group bars by", group);
      const start = field(body, "Agenda start hour (0–26)", Object.assign(document.createElement("input"),
        { type: "number", min: "0", max: "26", required: true, value: state.graphHourStart }));
      const end = field(body, "Agenda end hour (1–27; 25 is 01:00 next day)", Object.assign(document.createElement("input"),
        { type: "number", min: "1", max: "27", required: true, value: state.graphHourEnd }));
      const autofit = Object.assign(document.createElement("input"), { type: "checkbox", checked: state.graphAutofit });
      field(body, "Expand agenda hours to include sessions", autofit);
      const save = el("button", "primary wide", "Save");
      save.onclick = () => {
        if (!start.checkValidity() || !end.checkValidity() || Number(end.value) <= Number(start.value)) {
          return banner("Choose an end hour after the start, within 0–27.");
        }
        const regroup = group.value !== state.graphGroup;
        state.graphGroup = group.value;
        state.graphHourStart = Number(start.value);
        state.graphHourEnd = Number(end.value);
        state.graphAutofit = autofit.checked;
        saveGraphPreferences();
        closeSheet();
        if (regroup) graphChanged(); else renderGraphs();
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
    if (mounted) return;
    mounted = true;
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
      if (button.dataset.mode) button.onkeydown = (event) => {
        if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        const mode = event.key === "ArrowUp" ? "bars" : "agenda";
        const target = document.querySelector(`#view-graphs [data-mode="${mode}"]`);
        target.focus({ preventScroll: true });
        if (state.graphMode !== mode) target.click();
      };
    });
    $("graph-start").onchange = () => { state.graphStart = $("graph-start").value; graphChanged(); };
    $("graph-end").onchange = () => { state.graphEnd = $("graph-end").value; graphChanged(); };
    $("graph-options").onclick = graphOptionsSheet;
    graphFullscreen = createFullscreenController($("view-graphs"), { label: "Tracking graphs", onChange: (open) => {
      $("graph-fullscreen").setAttribute("aria-label", open ? "Close graphs fullscreen" : "Open graphs fullscreen");
      $("graph-fullscreen").setAttribute("aria-expanded", String(open));
      queueGraphResize();
    } });
    $("graph-fullscreen").onclick = () => graphFullscreen.toggle();
    window.addEventListener("resize", queueGraphResize);
    if (typeof ResizeObserver === "function") { observer = new ResizeObserver(queueGraphResize); observer.observe($("view-graphs")); }
  }
  function invalidate() { graphCache.clear(); }
  function reset() { graphRequest++; state.graphs = null; graphCache.clear(); agendaScroll.clear(); graphPaintKey = null; agendaLayout = null; }
  function destroy() {
    reset(); mounted = false;
    observer?.disconnect(); observer = null;
    window.removeEventListener("resize", queueGraphResize);
    graphFullscreen?.destroy(); graphFullscreen = null;
    if (graphResizeFrame !== null) cancelAnimationFrame(graphResizeFrame);
    graphResizeFrame = null;
    document.querySelectorAll("#view-graphs .seg-btn").forEach((button) => { button.onclick = button.onkeydown = null; });
    for (const id of ["graph-options", "graph-fullscreen"]) $(id).onclick = null;
    for (const id of ["graph-start", "graph-end"]) $(id).onchange = null;
  }
  return { mount, render: renderGraphs, load: loadGraphs, invalidate, reset, destroy, clock: updateAgendaNow, resize: queueGraphResize, get data() { return state.graphs; } };
}
