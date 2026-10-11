// Fresh shell when reachable, cached shell when offline. Authenticated data is
// never cached here: the app maintains an explicit mirror and queued edits, so
// a stale /api/context cannot masquerade as a live timer response.
const VERSION = "jobtracker-v6";
const SHELL = [
  "/", "/index.html", "/app.js", "/api.js", "/offline.js", "/styles.css",
  "/weight.js", "/weight-utils.js", "/weight.css", "/fullscreen.js", "/fullscreen.css",
  "/agenda-utils.js", "/manifest.webmanifest", "/icon.png",
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
