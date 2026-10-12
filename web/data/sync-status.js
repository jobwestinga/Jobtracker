// Per-device connection feedback; the durable queue remains the source of truth.
import { STORAGE_KEYS, accountTag } from "../shared/storage.js";

export function createSyncStatus({ getToken, pendingCount, onChange = () => {},
  storage = globalThis.localStorage, now = () => Date.now() }) {
  let token, phase = "checking", lastSuccessAt = null, error = "", sending = false;

  function checkAccount() {
    const current = getToken();
    if (current === token) return;
    token = current; phase = token ? "checking" : "disconnected";
    error = ""; sending = false; lastSuccessAt = null;
    try {
      const cached = JSON.parse(storage.getItem(STORAGE_KEYS.syncStatus) || "null");
      if (token && cached?.owner === accountTag(token) && Number.isFinite(cached.lastSuccessAt) &&
        cached.lastSuccessAt > 0 && cached.lastSuccessAt <= 8640000000000000) lastSuccessAt = cached.lastSuccessAt;
    } catch { /* status history is optional, unlike pending changes */ }
  }

  // A bare clock time from an earlier day would read as a sync made today.
  function syncedAt(at) {
    const stamp = new Date(at), clock = stamp.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return stamp.toDateString() === new Date(now()).toDateString() ? clock
      : `${stamp.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${clock}`;
  }

  function snapshot() {
    checkAccount();
    let pending = null;
    try { pending = pendingCount(); }
    catch (problem) { phase = "blocked"; error = problem.message || "Cannot read saved changes."; }
    const waiting = pending ? ` · ${pending} waiting` : "";
    let label;
    if (!token) label = "Disconnected";
    else if (phase === "offline") label = `Offline${waiting}`;
    else if (phase === "blocked") label = `Sync attention${waiting}`;
    else if (sending) label = `Sending${waiting}`;
    else if (pending) label = `Saved here${waiting}`;
    else if (phase === "ready" && lastSuccessAt) label = `Synced ${syncedAt(lastSuccessAt)}`;
    else label = "Checking sync";
    const last = lastSuccessAt ? new Date(lastSuccessAt).toLocaleString() : "No confirmed sync yet";
    return { phase: token ? phase : "disconnected", pending, lastSuccessAt, error, label,
      detail: [pending ? `${pending} change(s) saved on this phone and waiting to send.` : "", error,
        `Last successful sync: ${last}`].filter(Boolean).join(" ") };
  }
  function paint() {
    const value = snapshot();
    try { onChange(value); } catch { /* feedback cannot interrupt durable writes */ }
    return value;
  }
  function begin({ quiet = false } = {}) {
    checkAccount();
    if (!quiet || !lastSuccessAt) { phase = "checking"; error = ""; }
    return paint();
  }
  function success() {
    checkAccount();
    sending = false; error = ""; phase = "ready";
    // A newer operation may have arrived while a request was in flight.
    // Never call that newer work synced merely because an older batch finished.
    try {
      if (!pendingCount()) {
        lastSuccessAt = now();
        if (token) storage.setItem(STORAGE_KEYS.syncStatus,
          JSON.stringify({ owner: accountTag(token), lastSuccessAt }));
      }
    } catch { /* keep the timestamp in memory if optional status storage fails */ }
    return paint();
  }
  function failure(problem = {}) {
    checkAccount();
    sending = false; phase = problem.offline ? "offline" : "blocked";
    error = problem.message || problem.error || "Waiting to sync.";
    return paint();
  }
  function outbox(event) {
    checkAccount();
    if (event.type === "sending") { sending = true; phase = "checking"; error = ""; return paint(); }
    if (event.type === "result" && !event.ok) return failure(event);
    if (event.type === "result" && event.sent) return success();
    if (event.type === "result") sending = false;
    return paint();
  }
  return { snapshot, paint, begin, success, failure, outbox };
}
