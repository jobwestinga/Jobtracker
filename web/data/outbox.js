// All writes are durable before fetch; only acknowledgements remove entries.
import { api } from "./api.js";
import { ApiError } from "./errors.js";
import { uuid } from "../shared/ids.js";
import { STORAGE_KEYS, accountTag } from "../shared/storage.js";
import { getToken } from "./credentials.js";
const OUTBOX_KEY = STORAGE_KEYS.outbox;
const MAX_BATCH = 200;
const statusListeners = new Set();
export function subscribeOutbox(listener) {
  statusListeners.add(listener);
  return () => statusListeners.delete(listener);
}
function status(event) {
  for (const listener of statusListeners) {
    try { listener(event); } catch { /* UI observers cannot affect persistence */ }
  }
}

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
  status({ type: "queue", pending: entries.length });
}

function queueOwner() {
  try { return localStorage.getItem(STORAGE_KEYS.outboxOwner); }
  catch { throw new ApiError("Cannot read the connection for saved changes. Keep this browser's data.", 0); }
}

export function pendingTokenMatches(token) {
  if (!pendingCount()) return true;
  const owner = queueOwner();
  return Boolean(token) && (owner ? owner === accountTag(token) : token === getToken());
}

export function pendingForProjection() {
  const token = getToken(), owner = queueOwner(), queue = pendingOperations();
  if (!token || (owner && owner !== accountTag(token))) return [];
  // Another context can change localStorage between these synchronous reads.
  if (getToken() !== token || queueOwner() !== owner) return [];
  return queue;
}

function bindQueue(queue, token) {
  if (!token) throw new ApiError("Reconnect the original token to send saved changes.", 401);
  const tag = accountTag(token);
  const owner = queueOwner();
  if (queue.length && owner && owner !== tag) {
    throw new ApiError("Saved changes belong to another connection. Reconnect the original token to send them.", 409);
  }
  if (!queue.length || !owner) {
    // Write ownership before the queue, under the same lock. If this fails,
    // no new operation is saved or transmitted. Legacy queues bind on their
    // first replay using the existing credential; operation JSON stays intact.
    try { localStorage.setItem(STORAGE_KEYS.outboxOwner, tag); }
    catch { throw new ApiError("This browser cannot save changes. Free browser storage and try again.", 0); }
  }
}
export function pendingCount() { return pendingOperations().length; }

// Web Locks protect a browser tab and the home-screen app from draining the
// same queue at once. The single-flight promise below handles ordinary taps.
function withQueueLock(callback, name = OUTBOX_KEY) {
  return globalThis.navigator?.locks?.request
    ? navigator.locks.request(name, callback)
    : callback();
}

let flushing = null;

export async function send(op, params, { uid } = {}) {
  const token = getToken();
  const entry = { op_id: uuid(), op, params: uid ? { ...params, uid } : { ...params } };
  try {
    await withQueueLock(() => {
      if (getToken() !== token) throw new ApiError("The connection changed before saving. Try again with the original token.", 409);
      const queue = pendingOperations();
      bindQueue(queue, token);
      queue.push(entry);
      writeOutbox(queue);
    });
  } catch (error) {
    status({ type: "result", ok: false, offline: Boolean(error.offline), error: error.message });
    throw error;
  }
  try {
    const outcome = await flush();
    const queued = pendingOperations().some((item) => item.op_id === entry.op_id);
    return {
      ok: !queued,
      queued,
      result: outcome.applied?.find((item) => item.op_id === entry.op_id)?.result ?? null,
      error: queued ? outcome.error : undefined,
      offline: outcome.offline,
    };
  } catch (err) {
    // Enqueue already succeeded. A failed acknowledgement write must never
    // invite the form to create another UID/op_id: the server may have committed
    // this one, and its durable entry must retry with the same identity.
    return { ok: false, queued: true, result: null,
      error: err.message || "The saved change is waiting to finish syncing.", offline: Boolean(err.offline) };
  }
}

/** Drain only acknowledged entries. Never overwrite additions made mid-request. */
export function flush() {
  if (flushing) return flushing;
  flushing = Promise.resolve().then(() => withQueueLock(drain, `${OUTBOX_KEY}.drain`))
    .then((outcome) => { status({ type: "result", ...outcome }); return outcome; }, (error) => {
      status({ type: "result", ok: false, offline: Boolean(error.offline), error: error.message });
      throw error;
    })
    .finally(() => { flushing = null; });
  return flushing;
}

async function drain() {
  let sent = 0;
  const applied = [];
  while (true) {
    const token = getToken();
    const queue = await withQueueLock(() => {
      const saved = pendingOperations();
      if (!saved.length) return [];
      if (getToken() !== token) throw new ApiError("The connection changed while sending; saved changes are kept.", 409);
      bindQueue(saved, token);
      return saved.slice(0, MAX_BATCH);
    });
    if (!queue.length) return { ok: true, sent, applied };
    let response;
    let failure;
    status({ type: "sending" });
    try { response = await api.sendOps(queue, token); }
    catch (err) { failure = err; }
    const acknowledgements = response?.applied ?? failure?.applied ?? [];
    // /ops commits an ordered prefix. Accepting arbitrary matching IDs would
    // drop later edits after a malformed, duplicate or reordered receipt.
    if (!Array.isArray(acknowledgements) || acknowledgements.length > queue.length ||
      acknowledgements.some((item, index) => !item || item.op_id !== queue[index].op_id)) {
      return { ok: false, sent, applied,
        error: "The server returned invalid acknowledgements; your changes are kept." };
    }
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
