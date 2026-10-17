// One-import facade over the data layer for tests and tooling; app code imports the focused modules.
export { api } from "./data/api.js";
export { send, flush, pendingCount, pendingOperations, pendingForProjection, pendingTokenMatches } from "./data/outbox.js";
export { getToken, setToken, clearToken } from "./data/credentials.js";
export { ApiError } from "./data/errors.js";
export { uuid } from "./shared/ids.js";
