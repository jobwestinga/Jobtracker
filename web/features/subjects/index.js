import { $, el, safeColor } from "../../shared/dom.js";
import { localIso } from "../../shared/time.js";
import { hms, clockOf } from "../../shared/format.js";
import { openSheet, closeSheet, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";

export function createSubjectsFeature({ store, act, banner, reorderButtons, editSession }) {
  const state = { subjectFilter: "active", tick: null };
  const subjectsByUid = () => Object.fromEntries((store.snapshot?.subjects || []).map((subject) => [subject.uid, subject]));

  function renderToday() {
    $("subjects-active").classList.toggle("on", state.subjectFilter === "active");
    $("subjects-archived").classList.toggle("on", state.subjectFilter === "archived");
    $("add-subject").classList.toggle("hidden", state.subjectFilter === "archived");
    const card = $("active-card");
    const subjects = subjectsByUid();

    if (store.active) {
      const subject = subjects[store.active.subject_uid];
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
    const active = (store.snapshot?.subjects || [])
      .filter((s) => state.subjectFilter === "archived" ? s.is_archived : !s.is_archived)
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

    for (const subject of active) {
      const running = store.active?.subject_uid === subject.uid;
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

  function startTicking() {
    stopTicking();
    if (document.hidden) return;
    const started = store.active?.timer_origin ?? Date.now() - (store.active?.elapsed_seconds || 0) * 1000;
    const paint = () => { $("active-elapsed").textContent = hms((Date.now() - started) / 1000); };
    paint();
    state.tick = setInterval(paint, 1000);
  }

  function stopTicking() {
    if (state.tick) clearInterval(state.tick);
    state.tick = null;
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
        if ((store.snapshot?.subjects || []).some((row) => row.uid !== subject.uid && row.name === name.value.trim())) {
          return banner("That subject name already exists, including archived subjects.");
        }
        closeSheet();
        act("update_subject", { subject_uid: subject.uid, name: name.value.trim(), color: color.value, notes: notes.value });
      };
      body.append(save);
      reorderButtons(body, (store.snapshot?.subjects || []).filter((row) => Boolean(row.is_archived) === Boolean(subject.is_archived)),
        subject.uid, "set_subject_order", { archived: Boolean(subject.is_archived) });
      const history = el("button", "secondary wide", "Session history");
      history.onclick = () => subjectHistorySheet(subject);
      body.append(history);
      const archive = el("button", "secondary wide", subject.is_archived ? "Restore subject" : "Archive subject");
      archive.onclick = () => {
        if (!subject.is_archived && store.active?.subject_uid === subject.uid) {
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
      const sessions = (store.snapshot?.sessions || []).filter((row) => row.subject_uid === subject.uid)
        .sort((a, b) => b.start_time.localeCompare(a.start_time));
      if (!sessions.length) body.append(el("p", "muted", "No sessions yet."));
      for (const session of sessions) {
        const row = el("button", "ghost wide");
        row.append(el("div", "name", session.start_time.slice(0, 10)),
          el("div", "sub", `${clockOf(session.start_time)}–${clockOf(session.end_time)}${session.end_time ? "" : " · running"}`));
        row.disabled = !session.end_time;
        row.onclick = () => editSession(session);
        body.append(row);
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
      const used = new Set((store.snapshot?.subjects || []).filter((s) => !s.is_archived).map((s) => s.color));
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
        if ((store.snapshot?.subjects || []).some((row) => row.name === title)) {
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

  async function startTimer(subject) {
    let start = localIso(new Date());
    const uid = uuid();
    if (store.active) {
      if (!confirm(`Switch to ${subject.name}?`)) return;
      start = localIso(new Date());
      const sourceUid = store.active.session_uid || store.active.uid;
      await act("switch_subject", { subject_uid: subject.uid, start_time: start,
        end_time: start, source_session_uid: sourceUid }, {
        uid,
        optimistic: () => { store.active = { uid, session_uid: uid, subject_uid: subject.uid,
          start_time: start, elapsed_seconds: 0, timer_origin: Date.now() }; },
      });
      return;
    }
    // Show the timer running the moment it is tapped. Waiting for the round trip
    // made a start feel like it had not registered.
    await act("start_subject", { subject_uid: subject.uid, start_time: start }, {
      uid,
      optimistic: () => { store.active = { uid, session_uid: uid, subject_uid: subject.uid,
        start_time: start, elapsed_seconds: 0, timer_origin: Date.now() }; },
    });
  }

  async function stopTimer() {
    if (!store.active) return;
    // Send the phone's clock, so the server applies the sub-30-second rule to the
    // time the user actually stopped rather than to whenever the request lands.
    await act("stop_active_subject", { session_uid: store.active.session_uid || store.active.uid,
      end_time: localIso(new Date()) }, {
      optimistic: () => { store.active = null; },
    });
  }

  function mount() {
    $("subjects-active").onclick = () => { state.subjectFilter = "active"; renderToday(); };
    $("subjects-archived").onclick = () => { state.subjectFilter = "archived"; renderToday(); };
    $("stop-btn").onclick = stopTimer;
    $("add-subject").onclick = addSubjectSheet;
  }
  return { mount, render: renderToday, paintElapsed: () => { if (store.active) startTicking(); }, deactivate: stopTicking };
}
