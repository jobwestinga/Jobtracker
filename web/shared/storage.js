export const STORAGE_KEYS = Object.freeze({
  token: "jobtracker.token", outbox: "jobtracker.outbox", mirror: "jobtracker.mirror",
  graphs: "jobtracker.graphs", weightCache: "jobtracker.weight-cache",
  weightPreferences: "jobtracker.weight-preferences",
});
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
