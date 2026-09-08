// Service worker: makes the phone app open and stay readable with no signal.
//
// Strategy is network-first everywhere, falling back to the cache. The app is
// deployed by rsync and reloaded by hand, so a cache-first shell would keep
// serving yesterday's JavaScript long after a deploy — freshness matters more
// than the few milliseconds network-first costs over a fast local network.
//
// Writes are never cached or replayed here. The app already has its own ordered,
// idempotent outbox in localStorage; a second replay mechanism sitting in the
// service worker would be a way to send the same operation twice.

const VERSION = "jobtracker-v3";
const SHELL = [
  "/",
  "/index.html",
  "/app.js",
  "/api.js",
  "/styles.css",
  "/manifest.webmanifest",
  "/icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      // Take over straight away rather than waiting for every tab to close;
      // on a phone the app is usually the only tab and would otherwise sit on
      // the old worker indefinitely.
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== VERSION).map((name) => caches.delete(name)),
      ))
      .then(() => self.clients.claim()),
  );
});

/** Cache a copy, ignoring quota failures — a full cache must not break a request. */
async function remember(request, response) {
  try {
    const cache = await caches.open(VERSION);
    await cache.put(request, response.clone());
  } catch { /* out of space, or an opaque response */ }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;               // writes go to the outbox

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Reads of live data: fresh when possible, last-known when not. This is what
  // lets the app open on a plane and still show the history and goals.
  const isData = url.pathname.startsWith("/api/");

  event.respondWith((async () => {
    try {
      const response = await fetch(request);
      if (response && response.ok) await remember(request, response);
      return response;
    } catch (err) {
      const cached = await caches.match(request);
      if (cached) return cached;
      if (!isData && request.mode === "navigate") {
        const shell = await caches.match("/index.html");
        if (shell) return shell;
      }
      // Nothing cached: let the app's own error handling show "Offline".
      throw err;
    }
  })());
});
