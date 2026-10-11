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
