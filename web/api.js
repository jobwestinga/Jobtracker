// Talking to the JobTracker server from the phone.
//
// Same shape as the desktop client, for the same reasons: every write is an
// operation with a client-generated op_id, so a request that is retried after a
// dropped connection cannot be applied twice. Writes made with no signal wait in
// a localStorage outbox and go out in order when the connection returns.

const TOKEN_KEY = "jobtracker.token";
const OUTBOX_KEY = "jobtracker.outbox";

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(token) {
  try { localStorage.setItem(TOKEN_KEY, (token || "").trim()); } catch { /* private mode */ }
}
export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
    // No status means the request never reached the server: an outage, not a
    // refusal. Callers queue in the first case and complain in the second.
    this.offline = status === undefined || status === null;
  }
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

async function request(method, path, body) {
  const token = getToken();
  if (!token) throw new ApiError("no token", 401);
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(`cannot reach server: ${err.message}`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail ?? detail; } catch { /* not json */ }
    throw new ApiError(String(detail), response.status);
  }
  return response.status === 204 ? {} : response.json();
}

export const api = {
  context: () => request("GET", "/api/context"),
  snapshot: () => request("GET", "/api/snapshot"),
  active: () => request("GET", "/api/active"),
  day: (isoDate) => request("GET", `/api/sessions/day/${isoDate}`),
  breakdown: (grouping = "daily", days = 14) =>
    request("GET", `/api/graphs/breakdown?grouping=${grouping}&days=${days}`),
  agenda: (days = 7) => request("GET", `/api/graphs/agenda?days=${days}`),
  heatmap: () => request("GET", "/api/graphs/heatmap"),
  sendOps: (ops) => request("POST", "/ops", { ops }),
};

// ── outbox ──────────────────────────────────────────────────────────────

function readOutbox() {
  try { return JSON.parse(localStorage.getItem(OUTBOX_KEY) || "[]"); } catch { return []; }
}
function writeOutbox(entries) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(entries)); } catch { /* full */ }
}
export function pendingCount() {
  return readOutbox().length;
}

/**
 * Send one operation, queueing it if the server cannot be reached.
 *
 * Returns { ok, queued, result }. A refusal (4xx) is NOT queued: the server
 * decided, and retrying forever would block everything behind it.
 */
export async function send(op, params, { uid } = {}) {
  const entry = { op_id: uuid(), op, params: uid ? { ...params, uid } : params };
  const queue = readOutbox();

  // Anything already waiting must go first, or a delete could overtake the
  // create it refers to.
  if (queue.length) {
    queue.push(entry);
    writeOutbox(queue);
    const flushed = await flush();
    return { ok: flushed.ok, queued: !flushed.ok, result: null };
  }

  try {
    const response = await api.sendOps([entry]);
    return { ok: true, queued: false, result: response.applied?.[0]?.result ?? null };
  } catch (err) {
    if (err.offline) {
      writeOutbox([...queue, entry]);
      return { ok: false, queued: true, result: null };
    }
    throw err;
  }
}

/** Drain the outbox in order. Stops at the first refusal, keeping the rest. */
export async function flush() {
  let queue = readOutbox();
  if (!queue.length) return { ok: true, sent: 0 };

  try {
    await api.sendOps(queue);
    writeOutbox([]);
    return { ok: true, sent: queue.length };
  } catch (err) {
    if (err.offline) return { ok: false, sent: 0, offline: true };
    // A refusal names how far it got; drop what was applied and keep the rest
    // so the queue does not replay work the server already did.
    const appliedIds = new Set((err.applied || []).map((a) => a.op_id));
    queue = queue.filter((entry) => !appliedIds.has(entry.op_id));
    writeOutbox(queue);
    return { ok: false, sent: appliedIds.size, error: err.message };
  }
}

export { uuid };
