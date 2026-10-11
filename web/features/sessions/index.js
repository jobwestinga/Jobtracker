import { $, el, safeColor } from "../../shared/dom.js";
import { addDays, localIso } from "../../shared/time.js";
import { hm, clockOf, prettyDay as formatDay } from "../../shared/format.js";
import { openSheet, closeSheet, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";
import { logicalAgendaNow, dayStartHour } from "../graphs/agenda-time.js";

export function createSessionsFeature({ store, api, act, banner, saveMirror, navigateDay }) {
  const prettyDay = (iso) => formatDay(iso, store.context?.today);
  const state = { day: null, daySessions: [], dayStatus: "loading", dayCache: {} };
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

  function sessionSheet(session) {
    if (!session.end_time) return banner("Stop this running session before editing it.");
    openSheet("Edit session", (body) => {
      const subjects = (store.snapshot?.subjects || []).filter((s) => !s.is_archived || s.uid === session.subject_uid);
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
      const subjects = (store.snapshot?.subjects || []).filter((s) => !s.is_archived);
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
      const today = state.day === store.context?.today;
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

  function prepare(day = state.day) {
    state.day = day;
    state.daySessions = state.dayCache[day] || [];
    state.dayStatus = state.dayCache[day] ? "ready" : "loading";
  }
  function reset(cache = {}, day = null) {
    dayRequest++;
    state.dayCache = cache;
    state.day = day;
    state.daySessions = [];
  }
  function invalidate() { state.dayCache = {}; }
  function mount() {
    $("day-prev").onclick = () => navigateDay(addDays(state.day, -1));
    $("day-next").onclick = () => navigateDay(addDays(state.day, 1));
    $("day-date").onchange = () => { if ($("day-date").value) navigateDay($("day-date").value); };
    $("day-today").onclick = () => navigateDay(logicalAgendaNow(new Date(), dayStartHour({}, store.context?.day_start))?.day || store.context?.today);
    $("add-session").onclick = addSessionSheet;
  }
  return { mount, render: renderSessions, load: loadDay, prepare, reset, invalidate, edit: sessionSheet,
    get day() { return state.day; }, set day(value) { state.day = value; },
    get cache() { return state.dayCache; }, get status() { return state.dayStatus; } };
}
