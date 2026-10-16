import { PROFILE } from "./profile.js";

// Keep legacy keys for Weight, including old queued time operations. Never copy
// credentials or move/delete a queue when choosing another application.
export const STORAGE_KEYS = Object.freeze(Object.fromEntries(Object.entries({
  token: "jobtracker.token", outbox: "jobtracker.outbox", outboxOwner: "jobtracker.outbox-owner", mirror: "jobtracker.mirror",
  graphs: "jobtracker.graphs", weightCache: "jobtracker.weight-cache",
  syncStatus: "jobtracker.sync-status",
  weightPreferences: "jobtracker.weight-preferences",
}).map(([name, key]) => [name, key.replace(/^jobtracker/, PROFILE.storagePrefix)])));
export function accountTag(token) {
  // Keep cached data scoped to its credential without copying the credential
  // into another storage value. Two independent 32-bit hashes avoid accidental
  // cross-account cache reuse; the server still verifies every request.
  let a = 2166136261, b = 5381;
  for (const character of token || "") {
    a = Math.imul(a ^ character.charCodeAt(0), 16777619);
    b = Math.imul(b, 33) ^ character.charCodeAt(0);
  }
  return `${a >>> 0}:${b >>> 0}`;
}
