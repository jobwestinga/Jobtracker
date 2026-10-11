// All changes enter a durable, ordered outbox before the network sees them.
// A retry uses the same op_id, including after a reload or a lost response.
const TOKEN_KEY = "jobtracker.token";
const OUTBOX_KEY = "jobtracker.outbox";
const MAX_BATCH = 200;
const REQUEST_TIMEOUT_MS = 12000;

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(token) {
  try { localStorage.setItem(TOKEN_KEY, (token || "").trim()); }
  catch { throw new ApiError("This browser cannot save the access token.", 0); }
}
export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(message, status, payload = {}) {
    super(message);
    this.status = status;
    this.offline = status === undefined || status === null;
    this.retryable = this.offline || status >= 500 || status === 408 || status === 429;
    this.applied = payload.applied || [];
    this.failed_op_id = payload.failed_op_id;
    this.failed_index = payload.failed_index;
  }
}

export function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

async function request(method, path, body) {
  const token = getToken();
  if (!token) throw new ApiError("Connect this phone first.", 401);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      method,
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    let payload;
    try { payload = response.status === 204 ? {} : await response.json(); }
    catch {
      throw new ApiError("The server returned an unreadable response; your changes are kept.",
        response.ok ? undefined : response.status);
    }
    if (!response.ok) {
      throw new ApiError(String(payload.detail ?? response.statusText), response.status, payload);
    }
    if (getToken() !== token) throw new ApiError("The connection changed while loading; refresh to continue.", 409);
    return payload;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(controller.signal.aborted
      ? "The server took too long to reply."
      : `Cannot reach server: ${err.message}`);
  } finally {
    clearTimeout(timeout);
  }
}

function queryPath(path, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  }
  return `${path}?${query}`;
}

export const api = {
  context: () => request("GET", "/api/context"),
  snapshot: () => request("GET", "/api/snapshot?include_weights=false"),
  pull: (since) => request("GET", `/sync/pull?since=${encodeURIComponent(since)}`),
  active: () => request("GET", "/api/active"),
  weights: () => request("GET", "/api/weight"),
  day: (isoDate) => request("GET", `/api/sessions/day/${encodeURIComponent(isoDate)}`),
  breakdown: (grouping = "daily", days = 14, options = {}) =>
    request("GET", queryPath("/api/graphs/breakdown", { grouping, days, ...options })),
  agenda: (days = 7, options = {}) => request("GET", queryPath("/api/graphs/agenda", { days, ...options })),
  sendOps: (ops) => request("POST", "/ops", { ops }),
};

export function pendingOperations() {
  let entries;
  try { entries = JSON.parse(localStorage.getItem(OUTBOX_KEY) || "[]"); }
  catch { throw new ApiError("Cannot read saved changes. Keep this browser's data to recover them.", 0); }
  if (!Array.isArray(entries) || entries.some((entry) =>
    !entry || typeof entry.op_id !== "string" || typeof entry.op !== "string" ||
    !entry.params || typeof entry.params !== "object" || Array.isArray(entry.params))) {
    throw new ApiError("Saved changes are damaged. Keep this browser's data to recover them.", 0);
  }
  return entries;
}

function writeOutbox(entries) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(entries)); }
  catch {
    throw new ApiError("This browser cannot save changes. Free browser storage and try again.", 0);
  }
}
export function pendingCount() { return pendingOperations().length; }

// Web Locks protect a browser tab and the home-screen app from draining the
// same queue at once. The single-flight promise below handles ordinary taps.
function withQueueLock(callback, name = "jobtracker.outbox") {
  return globalThis.navigator?.locks?.request
    ? navigator.locks.request(name, callback)
    : callback();
}

let flushing = null;

export async function send(op, params, { uid } = {}) {
  const entry = { op_id: uuid(), op, params: uid ? { ...params, uid } : { ...params } };
  await withQueueLock(() => {
    const queue = pendingOperations();
    queue.push(entry);
    writeOutbox(queue);
  });
  const outcome = await flush();
  const queued = pendingOperations().some((item) => item.op_id === entry.op_id);
  return {
    ok: !queued,
    queued,
    result: outcome.applied?.find((item) => item.op_id === entry.op_id)?.result ?? null,
    error: queued ? outcome.error : undefined,
    offline: outcome.offline,
  };
}

/** Drain only acknowledged entries. Never overwrite additions made mid-request. */
export function flush() {
  if (flushing) return flushing;
  flushing = Promise.resolve().then(() => withQueueLock(drain, "jobtracker.outbox.drain"))
    .finally(() => { flushing = null; });
  return flushing;
}

async function drain() {
  let sent = 0;
  const applied = [];
  while (true) {
    const queue = pendingOperations().slice(0, MAX_BATCH);
    if (!queue.length) return { ok: true, sent, applied };
    let response;
    let failure;
    try { response = await api.sendOps(queue); }
    catch (err) { failure = err; }
    const batchIds = new Set(queue.map((item) => item.op_id));
    const acknowledgements = (response?.applied || failure?.applied || [])
      .filter((item) => batchIds.has(item.op_id));
    const acknowledged = new Set(acknowledgements.map((item) => item.op_id));
    if (acknowledged.size) {
      // Re-read: another action may have appended while fetch was pending.
      await withQueueLock(() => {
        writeOutbox(pendingOperations().filter((item) => !acknowledged.has(item.op_id)));
      });
      sent += acknowledged.size;
      applied.push(...acknowledgements);
    }
    if (failure) {
      return { ok: false, sent, applied, offline: failure.offline, retryable: failure.retryable,
        error: failure.message, failed_op_id: failure.failed_op_id };
    }
    if (acknowledged.size !== queue.length) {
      return { ok: false, sent, applied, error: "The server did not acknowledge all changes; they are kept." };
    }
  }
}
