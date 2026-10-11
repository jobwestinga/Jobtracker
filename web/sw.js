// Fresh shell when reachable, cached shell when offline. Authenticated data is
// never cached here: the app maintains an explicit mirror and queued edits, so
// a stale /api/context cannot masquerade as a live timer response.
const VERSION = "jobtracker-v7";
const SHELL = [
  "/",
  "/index.html",
  "/agenda-utils.js",
  "/api.js",
  "/app.js",
  "/data/api.js",
  "/data/credentials.js",
  "/data/errors.js",
  "/data/mirror.js",
  "/data/outbox.js",
  "/data/projection.js",
  "/data/transport.js",
  "/features/goals/index.js",
  "/features/goals/styles.css",
  "/features/graphs/agenda-time.js",
  "/features/graphs/index.js",
  "/features/graphs/styles.css",
  "/features/sessions/index.js",
  "/features/sessions/styles.css",
  "/features/settings/index.js",
  "/features/settings/styles.css",
  "/features/subjects/index.js",
  "/features/subjects/styles.css",
  "/features/templates/index.js",
  "/features/weight/index.js",
  "/features/weight/math.js",
  "/features/weight/picker.js",
  "/features/weight/projection.js",
  "/features/weight/styles.css",
  "/fullscreen.css",
  "/fullscreen.js",
  "/offline.js",
  "/shared/banner.js",
  "/shared/dom.js",
  "/shared/format.js",
  "/shared/fullscreen.css",
  "/shared/fullscreen.js",
  "/shared/ids.js",
  "/shared/order-controls.js",
  "/shared/sheet.js",
  "/shared/storage.js",
  "/shared/time.js",
  "/styles/controls.css",
  "/styles/shell.css",
  "/styles/tokens.css",
  "/styles.css",
  "/weight-utils.js",
  "/weight.css",
  "/weight.js",
  "/manifest.webmanifest",
  "/icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((names) => Promise.all(
    names.filter((name) => name.startsWith("jobtracker-") && name !== VERSION)
      .map((name) => caches.delete(name)),
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const cached = await cache.match(request) || (request.mode === "navigate" ? await cache.match("/index.html") : null);
    const controller = new AbortController();
    // A saved shell should not wait for an unreachable tailnet connection to
    // time out. Only use a deadline when we have a usable cached copy.
    const timeout = cached ? setTimeout(() => controller.abort(), 2000) : null;
    try {
      const response = await fetch(request, { signal: controller.signal, cache: "no-cache", redirect: "error" });
      if (response.ok) {
        try { await cache.put(request, response.clone()); } catch { /* cache is optional */ }
      }
      return response;
    } catch (err) {
      if (cached) return cached;
      throw err;
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  })());
});
