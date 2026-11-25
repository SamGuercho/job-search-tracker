// Service worker: makes the monitor installable (home-screen icon, own window) and lets the page open when the
// server can't be reached. Network first, so an update is always visible when online. The API is never cached:
// the monitor shows live telemetry, and flags and eval cases are written through it.
const CACHE = "monitor-shell-v1";
const SHELL = ["/", "/static/monitor.css", "/static/monitor.js", "/static/logo-64.png",
  "/manifest.webmanifest", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k.startsWith("monitor-shell-") && k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        }
        // an error from the server (e.g. 502: the monitor is off): use the saved copy
        return savedCopy(event.request).then((hit) => hit || response);
      })
      .catch(() => savedCopy(event.request).then((hit) => hit || Response.error())),
  );
});

// The saved copy of a file; for a page (navigation), the monitor itself (it is one page: "/").
async function savedCopy(request) {
  return (await caches.match(request)) || (request.mode === "navigate" ? caches.match("/") : undefined);
}
