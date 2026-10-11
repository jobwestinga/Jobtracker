import { el } from "./dom.js";

export function createOrderControls({ act, closeSheet }) {
  function reorderButtons(body, rows, uid, operation, params = {}) {
    const ordered = [...rows].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    const index = ordered.findIndex((row) => row.uid === uid);
    const controls = el("div", "row");
    for (const [text, offset] of [["Move up", -1], ["Move down", 1]]) {
      const button = el("button", "ghost", text);
      button.disabled = index + offset < 0 || index + offset >= ordered.length;
      button.onclick = () => {
        const next = [...ordered];
        [next[index], next[index + offset]] = [next[index + offset], next[index]];
        closeSheet();
        const key = operation === "set_subject_order" ? "subject_uids" : "goal_uids";
        act(operation, { ...params, [key]: next.map((row) => row.uid) });
      };
      controls.append(button);
    }
    body.append(el("label", null, "List order"), controls);
  }
  return reorderButtons;
}
