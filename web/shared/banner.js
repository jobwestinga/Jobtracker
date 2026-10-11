import { $ } from "./dom.js";

let bannerTimer = null;
export function banner(message, kind = "error", ms = 4000) {
  const node = $("banner");
  node.textContent = message;
  node.className = `banner ${kind === "ok" ? "ok" : ""}`;
  clearTimeout(bannerTimer);
  if (ms) bannerTimer = setTimeout(() => node.classList.add("hidden"), ms);
}

export function hideBanner() { $("banner").classList.add("hidden"); }

