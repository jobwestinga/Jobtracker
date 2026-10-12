export const $ = (id) => document.getElementById(id);

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function safeColor(value, fallback = "#3B82F6") {
  return typeof value === "string" && CSS.supports("color", value) ? value : fallback;
}

export function captureFocus(parent) {
  return parent.contains(document.activeElement) ? document.activeElement.dataset?.focusKey : null;
}

export function restoreFocus(parent, key, fallback = null) {
  if (!key) return;
  const target = [...parent.querySelectorAll('[data-focus-key]')].find((node) => node.dataset.focusKey === key) || fallback;
  target?.focus({ preventScroll: true });
}
