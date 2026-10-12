import { STORAGE_KEYS } from "../shared/storage.js";
import { ApiError } from "./errors.js";
const TOKEN_KEY = STORAGE_KEYS.token;

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

export function setToken(token) {
  try { localStorage.setItem(TOKEN_KEY, (token || "").trim()); }
  catch { throw new ApiError("This browser cannot save the access token.", 0); }
}

export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); }
  catch { throw new ApiError("This browser cannot remove the access token. This phone is still connected.", 0); }
}
