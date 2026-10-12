import { applyChanges, projectPending } from "./projection.js";
import { STORAGE_KEYS, accountTag } from "../shared/storage.js";
import { getToken as readToken } from "./credentials.js";

export function createMirror({ api, pendingOperations, getDayCache, getToken = readToken }) {
  const store = { snapshot: null, baseSnapshot: null, baseActive: null, active: null, context: null, head: null };
  let token = getToken();
  function checkAccount() {
    if (getToken() !== token) reset();
  }

  function saveMirror() {
    checkAccount();
    try {
      localStorage.setItem(STORAGE_KEYS.mirror, JSON.stringify({
        owner: accountTag(getToken()), snapshot: store.baseSnapshot, context: store.context, head: store.head,
        dayCache: getDayCache(),
      }));
    } catch { /* the durable outbox reports storage failures; cache is optional */ }
  }

  function projectOutbox() {
    checkAccount();
    const projected = projectPending(store.baseSnapshot, store.baseActive, pendingOperations());
    store.snapshot = projected.snapshot;
    store.active = projected.active;
  }

  async function updateMirror(force, context) {
    checkAccount();
    if (force || !store.baseSnapshot || context.head < store.head) {
      store.baseSnapshot = await api.snapshot();
      store.head = store.baseSnapshot.head ?? context.head;
      return true;
    }
    // The feed gives only changed rows, rather than re-downloading a half-MB
    // snapshot after each checkbox or timer tap. Apply a complete pass atomically.
    let cursor = store.head;
    let snapshot = store.baseSnapshot;
    let changed = false;
    do {
      const delta = await api.pull(cursor);
      snapshot = applyChanges(snapshot, delta.changes || [], delta.settings || {});
      changed ||= Boolean(delta.changes?.length) || JSON.stringify(delta.settings || {}) !==
        JSON.stringify(store.baseSnapshot.settings || {});
      if (delta.more && delta.seq <= cursor) throw new Error("Sync did not advance; saved data is kept.");
      cursor = delta.seq;
      if (!delta.more) break;
    } while (true);
    store.baseSnapshot = snapshot;
    store.head = cursor;
    return changed;
  }

  function restore() {
    checkAccount();
    const cached = JSON.parse(localStorage.getItem(STORAGE_KEYS.mirror) || "null");
    // A cache is optional; an untagged legacy cache cannot prove its account.
    // Ignore it until a fresh authenticated read without touching saved writes.
    if (!getToken() || !cached?.snapshot || cached.owner !== accountTag(getToken())) return null;
    store.baseSnapshot = cached.snapshot;
    store.context = cached.context;
    store.head = cached.head;
    store.baseActive = cached.context?.active ? { ...cached.context.active,
      timer_origin: new Date(cached.context.active.start_time).getTime() } : null;
    projectOutbox();
    return cached;
  }
  function reset() {
    token = getToken();
    Object.assign(store, { snapshot: null, baseSnapshot: null, baseActive: null, active: null, context: null, head: null });
  }
  return { store, save: saveMirror, project: projectOutbox, update: updateMirror, restore, reset };
}
