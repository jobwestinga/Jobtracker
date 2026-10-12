import { $, el } from "./dom.js";

let returnFocus = null, background = [], bound = false;
const sheetDialog = () => $("sheet").querySelector?.(".sheet") || $("sheet");
const visible = (element) => element && !element.hidden && (!element.getClientRects || element.getClientRects().length);
function focusables(dialog) {
  return [...(dialog.querySelectorAll?.('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]') || [])]
    .filter((element) => element.tabIndex !== -1 && visible(element));
}
function showSheet(focus = null) {
  const sheet = $("sheet"), dialog = sheetDialog(), title = $("sheet-title");
  sheet.classList.remove("hidden");
  dialog.setAttribute?.("role", "dialog"); dialog.setAttribute?.("aria-modal", "true");
  dialog.setAttribute?.("aria-labelledby", "sheet-title"); dialog.setAttribute?.("tabindex", "-1");
  title?.setAttribute?.("tabindex", "-1");
  if (!background.length) {
    for (const id of ["app", "setup"]) {
      const element = $(id);
      if (element && !element.contains?.(sheet)) { background.push([element, Boolean(element.inert)]); element.inert = true; }
    }
  }
  (visible(focus) ? focus : title || dialog).focus?.({ preventScroll: true });
}

export function openSheet(title, build) {
  const sheet = $("sheet");
  if (sheet.classList.contains("hidden")) returnFocus = document.activeElement;
  $("sheet-title").textContent = title;
  const body = $("sheet-body");
  body.innerHTML = "";
  build(body);
  showSheet();
}

export function closeSheet() {
  const sheet = $("sheet");
  if (sheet.classList.contains("hidden")) return;
  sheet.classList.add("hidden");
  for (const [element, inert] of background) element.inert = inert;
  background = [];
  const target = returnFocus?.isConnected && visible(returnFocus) ? returnFocus :
    document.querySelector?.('.tab.on, #refresh, #open-settings');
  target?.focus?.({ preventScroll: true });
}

/** One mutation owns a sheet until it is durable. Restore this exact draft
 * only when enqueue failed; never reopen over a newly opened sheet.
 */
export function createSheetAction(content, { act, onError = () => {} }) {
  let submitted = false;
  const canSubmit = () => !submitted && content.isConnected && !$("sheet").classList.contains("hidden");
  const submit = async (op, params, options) => {
    if (!canSubmit()) return;
    const draftFocus = document.activeElement;
    submitted = true;
    closeSheet();
    let outcome;
    try { outcome = await act(op, params, options); }
    catch (error) {
      try { onError(error); } catch { /* preserve retry access if display fails */ }
      outcome = { ok: false };
    }
    if (outcome?.ok === false && !outcome.queued && content.isConnected) {
      submitted = false;
      showSheet(draftFocus);
    }
    return outcome;
  };
  submit.canSubmit = canSubmit;
  return submit;
}

let fieldId = 0;
export function field(parent, label, input) {
  const wrap = el("div", "field");
  input.id ||= `field-${++fieldId}`;
  const caption = el("label", null, label);
  caption.htmlFor = input.id;
  wrap.append(caption, input);
  parent.append(wrap);
  return input;
}

export function bindSheet() {
  if (bound) return;
  bound = true;
  $("sheet-close").onclick = closeSheet;
  $("sheet").onclick = (event) => { if (event.target === $("sheet")) closeSheet(); };
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || $("sheet").classList.contains("hidden")) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeSheet(); return; }
    if (event.key !== "Tab") return;
    const dialog = sheetDialog(), items = focusables(dialog), first = items[0], last = items.at(-1), focused = document.activeElement;
    if (!first) { event.preventDefault(); dialog.focus?.(); return; }
    if (!dialog.contains(focused) || !items.includes(focused) ||
        (event.shiftKey && focused === first) || (!event.shiftKey && focused === last)) {
      event.preventDefault(); (event.shiftKey ? last : first).focus({ preventScroll: true });
    }
  });
  document.addEventListener("focusin", (event) => {
    if ($("sheet").classList.contains("hidden") || sheetDialog().contains(event.target)) return;
    ($("sheet-title") || sheetDialog()).focus?.({ preventScroll: true });
  });
}
