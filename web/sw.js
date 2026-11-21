// Service worker: makes the app installable and lets its shell open offline.
// Network first (an update is always visible when online); never caches your data or files.
const CACHE = "jobtracker-shell-v2";
const SHELL = ["/", "/css/styles.css", "/js/app.js", "/js/api.js", "/js/ui.js", "/favicon.svg", "/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isData = url.pathname.startsWith("/api/") || url.pathname.startsWith("/files/")
    || url.pathname.startsWith("/login") || url.pathname.startsWith("/logout");
  if (event.request.method !== "GET" || url.origin !== location.origin || isData) return;  // straight to the network
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/"))),
  );
});

// Notifications sent by the server (prep ready, debrief ready, interview and follow-up reminders)
self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch { d = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(d.title || "Job Search", {
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
