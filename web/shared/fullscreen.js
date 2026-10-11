// Full-viewport plots work in a standalone iPhone app as well as browsers that
// support the Fullscreen API. Keep the original DOM so chart/event references,
// open options and a weighing draft survive entering and leaving fullscreen.
let active = null;
let nativePending = null;
let nativeOwned = false;
let nativeExitPending = false;

function releaseNative() {
  if (!nativeOwned || nativeExitPending || active ||
      document.fullscreenElement !== document.documentElement) return;
  nativeExitPending = true;
  try {
    Promise.resolve(document.exitFullscreen?.()).catch(() => {}).finally(() => {
      nativeExitPending = false;
      nativeOwned = document.fullscreenElement === document.documentElement;
    });
  } catch {
    nativeExitPending = false;
  }
}

function requestNative() {
  if (nativePending || document.fullscreenElement ||
      typeof document.documentElement.requestFullscreen !== "function") return;
  try {
    // The root includes the app's edit/settings sheet, which must remain usable
    // above the plot. A rejected request simply leaves the viewport fallback.
    nativePending = Promise.resolve(document.documentElement.requestFullscreen())
      .then(() => {
        nativeOwned = document.fullscreenElement === document.documentElement;
        releaseNative(); // A delayed native request may finish after Close.
      }).catch(() => {}).finally(() => { nativePending = null; });
  } catch { nativePending = null; }
}

export function closeFullscreen() { active?.close(); }

export function createFullscreenController(element, { onChange = () => {} } = {}) {
  let open = false, destroyed = false, previousFocus = null, previousScroll = 0;
  const scrollFrame = document.querySelector("#views");
  const oldTabindex = element.getAttribute("tabindex");
  const oldRole = element.getAttribute("role");
  const oldModal = element.getAttribute("aria-modal");

  function restoreAttribute(name, value) {
    if (value === null) element.removeAttribute(name);
    else element.setAttribute(name, value);
  }

  function close() {
    if (!open) return;
    open = false;
    if (active === controller) active = null;
    element.classList.remove("plot-fullscreen");
    if (!active) document.body.classList.remove("has-plot-fullscreen");
    restoreAttribute("tabindex", oldTabindex);
    restoreAttribute("role", oldRole);
    restoreAttribute("aria-modal", oldModal);
    if (scrollFrame) scrollFrame.scrollTop = previousScroll;
    onChange(false);
    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    previousFocus = null;
    releaseNative();
  }

  function toggle() {
    if (destroyed) return;
    if (open) { close(); return; }
    const incomingFocus = document.activeElement;
    active?.close();
    previousFocus = incomingFocus;
    previousScroll = scrollFrame?.scrollTop || 0;
    open = true;
    active = controller;
    element.classList.add("plot-fullscreen");
    document.body.classList.add("has-plot-fullscreen");
    element.setAttribute("tabindex", "-1");
    element.setAttribute("role", "dialog");
    element.setAttribute("aria-modal", "true");
    onChange(true);
    element.focus({ preventScroll: true });
    requestNative();
  }

  function onKey(event) {
    if (!open || active !== controller) return;
    // A sheet is above the plot and has its own keyboard handling.
    const sheet = document.querySelector("#sheet");
    if (sheet && !sheet.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault(); close(); return;
    }
    if (event.key !== "Tab") return;
    const items = [...element.querySelectorAll(
      'button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href], summary, [tabindex="0"]',
    )].filter((item) => !item.hidden && (!item.getClientRects || item.getClientRects().length));
    const first = items[0], last = items.at(-1);
    if (!first) { event.preventDefault(); element.focus(); return; }
    const focused = document.activeElement;
    if (!element.contains(focused) || focused === element ||
        (event.shiftKey && focused === first) || (!event.shiftKey && focused === last)) {
      event.preventDefault(); (event.shiftKey ? last : first).focus();
    }
  }

  function onNativeChange() {
    if (active !== controller) return;
    if (!nativeOwned || document.fullscreenElement === document.documentElement) return;
    nativeOwned = false;
    // An earlier Close may still be leaving native fullscreen when a different
    // plot opens. Do not close that newer plot's viewport fallback.
    if (!nativeExitPending && active === controller) close();
  }

  const controller = {
    toggle, close,
    get isOpen() { return open; },
    destroy() {
      if (destroyed) return;
      close(); destroyed = true;
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onNativeChange);
    },
  };
  document.addEventListener("keydown", onKey);
  document.addEventListener("fullscreenchange", onNativeChange);
  return controller;
}
