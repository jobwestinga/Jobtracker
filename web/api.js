// Compatibility entrypoint; features may import the focused modules directly.
export { api } from "./data/api.js";
export { send, flush, pendingCount, pendingOperations } from "./data/outbox.js";
export { getToken, setToken, clearToken } from "./data/credentials.js";
export { ApiError } from "./data/errors.js";
export { uuid } from "./shared/ids.js";
