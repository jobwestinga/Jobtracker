import { el } from "../../shared/dom.js";
import { hm } from "../../shared/format.js";
import { CUSTOM_DURATION_CHUNKS } from "./duration.js";

/** Own the selection DOM/draft. The session sheet supplies the write action. */
export function createDurationPicker({ onConfirm }) {
  const custom = el("details", "session-custom");
  custom.append(el("summary", null, "Custom duration"));
  const chunks = el("div", "session-duration-chunks");
  chunks.setAttribute("role", "group");
  chunks.setAttribute("aria-label", "Choose duration chunks; each can be selected once");
  const selected = new Set();
  const total = el("output", "session-custom-total", "Choose duration chunks");
  total.id = "session-custom-total";
  total.setAttribute("aria-live", "polite");
  total.setAttribute("aria-atomic", "true");
  const clear = el("button", "secondary", "Clear");
  clear.type = "button";
  const confirm = el("button", "primary", "Add, ending now");
  confirm.type = "button";
  const minutes = () => [...selected].reduce((sum, value) => sum + value, 0);
  const paint = () => {
    const sum = minutes();
    for (const button of chunks.children) button.setAttribute("aria-pressed", String(selected.has(Number(button.dataset.minutes))));
    total.textContent = sum ? `${hm(sum * 60)} · ${sum} min` : "Choose duration chunks";
    clear.disabled = confirm.disabled = sum === 0;
  };
  for (const [label, duration] of CUSTOM_DURATION_CHUNKS) {
    const button = el("button", "chip", label);
    button.type = "button";
    button.dataset.minutes = duration;
    button.setAttribute("aria-pressed", "false");
    button.onclick = () => {
      if (selected.has(duration)) selected.delete(duration); else selected.add(duration);
      paint();
    };
    chunks.append(button);
  }
  clear.onclick = () => { selected.clear(); paint(); };
  confirm.onclick = () => { const sum = minutes(); if (sum) return onConfirm(sum); };
  const actions = el("div", "session-custom-actions");
  actions.append(clear, confirm);
  custom.append(chunks, total, actions);
  paint();
  return custom;
}
