import { getToken } from "./credentials.js";
import { ApiError } from "./errors.js";
const REQUEST_TIMEOUT_MS = 12000;

export async function request(method, path, body, { expectedToken } = {}) {
  const token = expectedToken ?? getToken();
  if (!token) throw new ApiError("Connect this phone first.", 401);
  if (getToken() !== token) throw new ApiError("The connection changed while sending; saved changes are kept.", 409);
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
    // Error responses may contain receipts too. Reject either kind of stale
    // response before another account's acknowledgements reach the outbox.
    if (getToken() !== token) throw new ApiError("The connection changed while loading; refresh to continue.", 409);
    if (!response.ok) {
      throw new ApiError(String(payload.detail ?? response.statusText), response.status, payload);
    }
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

export function queryPath(path, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  }
  return `${path}?${query}`;
}
