// Service worker: makes the app installable and lets it open when the server can't be reached.
// Network first (an update is always visible when online). The app's code is all saved at install, so every
// page opens offline; the data shown offline is saved by the page itself (api.js), in another cache.
const CACHE = "jobtracker-shell-v11";
const SHELL = ["/", "/css/styles.css", "/js/app.js", "/js/api.js", "/js/ui.js", "/js/views/analytics.js", "/js/views/chat.js", "/js/views/company.js", "/js/views/costs.js", "/js/views/cv.js", "/js/views/import.js", "/js/views/master.js", "/js/views/practice.js", "/js/views/process-graph.js", "/js/views/recordings.js", "/js/views/today.js", "/js/views/tracker.js", "/manifest.webmanifest", "/favicon.svg", "/icons/icon-192.png", "/icons/logo-64.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // old versions of the app's code only: the offline data (jobtracker-data-*) must survive updates
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k.startsWith("jobtracker-shell-") && k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isLogo = /^\/files\/.+\/logo\.(png|svg)$/.test(url.pathname);  // company logos: kept for offline reading
  const isData = url.pathname.startsWith("/api/") || (url.pathname.startsWith("/files/") && !isLogo)
    || url.pathname.startsWith("/login") || url.pathname.startsWith("/logout");
  if (event.request.method !== "GET" || url.origin !== location.origin || isData) return;  // straight to the network
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        }
        // the server answered with an error (e.g. 502: the home server's app is off): use the saved copy
        return savedCopy(event.request).then((hit) => hit || response);
      })
      .catch(() => savedCopy(event.request).then((hit) => hit || Response.error())),
  );
});

// The saved copy of a file; for a page (navigation), the app itself (it is one page: "/").
async function savedCopy(request) {
  return (await caches.match(request)) || (request.mode === "navigate" ? caches.match("/") : undefined);
}

// Notifications sent by the server (prep ready, debrief ready, interview and follow-up reminders)
self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch { d = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(d.title || "Ezri", {
    body: d.body || "", tag: d.tag || undefined, data: { url: d.url || "/" },
    icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
  }));
});

// Tapping a notification opens the page it is about (in the open app if there is one)
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((w) => w.url.startsWith(self.location.origin));
    if (open) {
      try { await open.navigate(url); } catch { /* not controlled yet: focus is enough */ }
      return open.focus();
    }
    return self.clients.openWindow(url);
  })());
});
