import { $, el, safeColor } from "../../shared/dom.js";
import { addDays, localIso } from "../../shared/time.js";
import { hm, clockOf, prettyDay as formatDay } from "../../shared/format.js";
import { openSheet, createSheetAction, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";
import { logicalAgendaNow, dayStartHour } from "../graphs/agenda-time.js";
import { QUICK_DURATIONS, endingNowRange, manualDurationSeconds } from "./duration.js";
import { createDurationPicker } from "./duration-picker.js";

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

  function validSessionInputs(start, end, subject, range) {
    for (const input of [start, end, subject]) {
      if (!input.checkValidity()) { input.reportValidity(); return false; }
    }
    if (!subject.value) { banner("Choose a subject first."); return false; }
    if (!start.value || !end.value || end.value < start.value) {
      banner("End must be at or after start. Include the next date for overnight sessions.");
      return false;
    }
    return hasMinimumDuration(range);
  }

  function hasMinimumDuration(range) {
    const seconds = manualDurationSeconds(range.start_time, range.end_time);
    if (!Number.isFinite(seconds) || seconds < 30) {
      banner("A session must last at least 30 seconds.");
      return false;
    }
    return true;
  }

  // Name the saved range and, when the start falls before the day boundary,
  // the earlier tracking day it counts towards, so the row is never "missing".
  function quickAddReceipt(subject_uid, range) {
    const subject = (store.snapshot?.subjects || []).find((row) => row.uid === subject_uid);
    const boundary = dayStartHour({}, store.context?.day_start);
    const day = logicalAgendaNow(new Date(range.start_time), boundary)?.day;
    const today = logicalAgendaNow(new Date(), boundary)?.day;
    const seconds = manualDurationSeconds(range.start_time, range.end_time);
    const counted = day && today && day !== today ? ` · tracking day: ${formatDay(day, today)}` : "";
    return `Added ${hm(seconds)}${subject ? ` to ${subject.name}` : ""} · ${clockOf(range.start_time)}–${clockOf(range.end_time)}${counted}`;
  }

  function quickAddControls(content, subjectUid, submit) {
    const add = async (minutes) => {
      if (!submit.canSubmit()) return;
      const subject_uid = subjectUid();
      if (!subject_uid) return banner("Choose a subject first.");
      const range = endingNowRange(minutes);
      const outcome = await submit("add_session", { subject_uid, ...range }, { uid: uuid() });
      // Queued and refused outcomes already have their own banner.
      if (outcome?.ok && !outcome.result?.reason) banner(quickAddReceipt(subject_uid, range), "ok");
      return outcome;
    };
    const quick = el("div", "session-quick-presets");
    quick.setAttribute("role", "group");
    quick.setAttribute("aria-label", "Quick add, ending now");
    for (const [label, minutes] of QUICK_DURATIONS) {
      const chip = el("button", "chip", label);
      chip.type = "button";
      chip.dataset.minutes = minutes;
      chip.setAttribute("aria-label", `Add ${minutes} minutes, ending now`);
      chip.onclick = () => add(minutes);
      quick.append(chip);
    }
    content.append(el("p", "session-caption", "Quick add, ending now"), quick);
    content.append(createDurationPicker({ onConfirm: add }));
  }

  function quickAdd(subject) {
    const selected = (store.snapshot?.subjects || []).find((row) => row.uid === subject?.uid && !row.is_archived);
    if (!selected) return banner("Choose an active subject first.");
    openSheet(`Add time · ${selected.name}`, (body) => {
      const content = el("div", "session-form");
      body.append(content);
      const submit = createSheetAction(content, { act, onError: (error) => banner(error.message || "That did not work") });
      quickAddControls(content, () => selected.uid, submit);
    });
  }

  function sessionSheet(session) {
    if (!session.end_time) return banner("Stop this running session before editing it.");
    openSheet("Edit session", (body) => {
      const content = el("div", "session-form");
      body.append(content);
      const submit = createSheetAction(content, { act, onError: (error) => banner(error.message || "That did not work") });
      const subjects = (store.snapshot?.subjects || []).filter((s) => !s.is_archived || s.uid === session.subject_uid);
      const select = document.createElement("select");
      for (const subject of subjects) {
        const option = el("option", null, subject.name);
        option.value = subject.uid;
        if (subject.uid === session.subject_uid) option.selected = true;
        select.append(option);
      }
      field(content, "Subject", select);

      const row = el("div", "session-times");
      const originalStart = session.start_time, originalEnd = session.end_time;
      const start = Object.assign(document.createElement("input"),
        { type: "datetime-local", step: "1", required: true, value: originalStart.slice(0, 19) });
      const end = Object.assign(document.createElement("input"),
        { type: "datetime-local", step: "1", required: true, value: originalEnd.slice(0, 19) });
      const displayedStart = start.value, displayedEnd = end.value;
      field(row, "Start", start);
      field(row, "End", end);
      content.append(row);

      const nudges = el("div", "session-nudges");
      nudges.setAttribute("role", "group");
      nudges.setAttribute("aria-label", "Move the whole session");
      for (const [label, seconds] of [["−1 day", -86400], ["−1h", -3600], ["−15m", -900],
        ["+15m", 900], ["+1h", 3600], ["+1 day", 86400]]) {
        const chip = el("button", "chip", label);
        chip.type = "button";
        chip.dataset.seconds = seconds;
        chip.setAttribute("aria-label", `Move session ${Math.abs(seconds) === 86400 ? "1 day" : label.slice(1)} ${seconds < 0 ? "earlier" : "later"}`);
        chip.onclick = () => {
          if (!hasMinimumDuration({ start_time: originalStart, end_time: originalEnd })) return;
          return submit("shift_session", { session_uid: session.uid, seconds });
        };
        nudges.append(chip);
      }
      content.append(el("p", "session-caption", "Move both start and end"), nudges);

      const save = el("button", "primary wide", "Save");
      save.onclick = async () => {
        const params = {
          session_uid: session.uid,
          subject_uid: select.value,
          // A subject-only save must preserve imported fractional timestamps.
          start_time: start.value === displayedStart ? originalStart : start.value.length === 16 ? `${start.value}:00` : start.value,
          end_time: end.value === displayedEnd ? originalEnd : end.value.length === 16 ? `${end.value}:00` : end.value,
        };
        if (!validSessionInputs(start, end, select, params)) return;
        await submit("update_session", params);
      };
      content.append(save);

      const dup = el("button", "secondary wide", "Duplicate to today");
      dup.onclick = async () => {
        if (!hasMinimumDuration({ start_time: originalStart, end_time: originalEnd })) return;
        await submit("duplicate_session", { session_uid: session.uid, to: "today" }, { uid: uuid() });
      };
      content.append(dup);

      const del = el("button", "danger wide", "Delete session");
      del.onclick = async () => {
        if (!content.isConnected || $("sheet").classList.contains("hidden")) return;
        if (!confirm("Delete this session?")) return;
        await submit("delete_session", { session_uid: session.uid });
      };
      content.append(del);
    });
  }

  function addSessionSheet() {
    openSheet("Add session", (body) => {
      const content = el("div", "session-form");
      body.append(content);
      const submit = createSheetAction(content, { act, onError: (error) => banner(error.message || "That did not work") });
      const subjects = (store.snapshot?.subjects || []).filter((s) => !s.is_archived);
      if (!subjects.length) {
        content.append(el("p", "muted", "Add a subject in the Subjects tab first."));
        return;
      }
      const select = document.createElement("select");
      for (const subject of subjects) {
        const option = el("option", null, subject.name);
        option.value = subject.uid;
        select.append(option);
      }
      field(content, "Subject", select);

      const now = new Date();
      const from = endingNowRange(60, now).start_time;
      const today = state.day === store.context?.today;
      const row = el("div", "session-times");
      const start = Object.assign(document.createElement("input"),
        { type: "datetime-local", step: "1", required: true, value: today ? from.slice(0, 16) : `${state.day}T09:00` });
      const end = Object.assign(document.createElement("input"),
        { type: "datetime-local", step: "1", required: true, value: today ? localIso(now).slice(0, 16) : `${state.day}T10:00` });
      field(row, "Start", start);
      field(row, "End", end);

      if (today) quickAddControls(content, () => select.value, submit);
      content.append(el("p", "session-caption", "Exact times"), row);

      const save = el("button", "primary wide", "Add with these times");
      save.onclick = async () => {
        const params = {
          subject_uid: select.value,
          start_time: start.value.length === 16 ? `${start.value}:00` : start.value,
          end_time: end.value.length === 16 ? `${end.value}:00` : end.value,
        };
        if (!validSessionInputs(start, end, select, params)) return;
        await submit("add_session", params, { uid: uuid() });
      };
      content.append(save);
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
  return { mount, render: renderSessions, load: loadDay, prepare, reset, invalidate, edit: sessionSheet, quickAdd,
    get day() { return state.day; }, set day(value) { state.day = value; },
    get cache() { return state.dayCache; }, get status() { return state.dayStatus; } };
}
