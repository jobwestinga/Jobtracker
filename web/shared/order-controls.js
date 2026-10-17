import { el } from "./dom.js";

// The Mac's subject order (tasks ORDER BY sort_order ASC, created_at DESC).
// Snapshots arrive in database order, so every subject list sorts through here.
export function orderSubjects(rows) {
  return [...(rows || [])].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) ||
    String(b.created_at || "").localeCompare(String(a.created_at || "")));
}

export function createOrderControls({ act, closeSheet }) {
  function reorderButtons(body, rows, uid, operation, params = {}, submit = null) {
    const ordered = operation === "set_subject_order" ? orderSubjects(rows) :
      [...rows].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    const index = ordered.findIndex((row) => row.uid === uid);
    const controls = el("div", "row");
    const caption = el("p", "caption", "List order");
    caption.id = "order-controls-label";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-labelledby", caption.id);
    for (const [text, offset] of [["Move up", -1], ["Move down", 1]]) {
      const button = el("button", "ghost", text);
      button.disabled = index < 0 || index + offset < 0 || index + offset >= ordered.length;
      button.onclick = () => {
        if (button.disabled || (submit && !submit.canSubmit())) return;
        const next = [...ordered];
        [next[index], next[index + offset]] = [next[index + offset], next[index]];
        const key = operation === "set_subject_order" ? "subject_uids" : "goal_uids";
        const payload = { ...params, [key]: next.map((row) => row.uid) };
        if (submit) return submit(operation, payload);
        closeSheet();
        return act(operation, payload);
      };
      controls.append(button);
    }
    body.append(caption, controls);
  }
  return reorderButtons;
}
