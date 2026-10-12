import { request, queryPath } from "./transport.js";

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
  sendOps: (ops, expectedToken) => request("POST", "/ops", { ops }, { expectedToken }),
};
