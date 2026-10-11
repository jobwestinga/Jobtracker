import { $, el } from "../../shared/dom.js";
import { openSheet, closeSheet, field } from "../../shared/sheet.js";
import { uuid } from "../../shared/ids.js";

export function createTemplatesFeature({ store, act: applyWrite, send }) {
  const act = (op, params, options = {}) => applyWrite(op, params, {
    ...options, afterAcknowledged: ["add_template", "update_template", "set_template_active"].includes(op)
      ? () => send("generate_due_goals", {}) : undefined,
  });

  function templatesSheet() {
    openSheet("Recurring goals", (body) => {
      const templates = store.snapshot?.templates || [];
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

  return { mount: () => { $("goal-templates").onclick = templatesSheet; }, open: templatesSheet };
}
