import { $, el } from "./dom.js";

export function openSheet(title, build) {
  $("sheet-title").textContent = title;
  const body = $("sheet-body");
  body.innerHTML = "";
  build(body);
  $("sheet").classList.remove("hidden");
}

export function closeSheet() { $("sheet").classList.add("hidden"); }

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
  $("sheet-close").onclick = closeSheet;
  $("sheet").onclick = (event) => { if (event.target === $("sheet")) closeSheet(); };
}
