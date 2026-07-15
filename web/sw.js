/* Zurys service worker — drží PWA shell dostupný i při výpadku sítě. API zůstává vždy online. */
const CACHE = "zurys-shell-2026071238";   // digity = cache verze; bumpuje deploy.py spolu s index.html
const APP_SHELL = [
  "/", "/index.html", "/manifest.json", "/sedlak-cut.png",
  "/styles.css?v=2026071238", "/farm.css?v=2026071238", "/app.js?v=2026071238",
];

self.addEventListener("install", (event) => event.waitUntil(
  caches.open(CACHE).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
));
self.addEventListener("activate", (event) => event.waitUntil(
  caches.keys().then((keys) => Promise.all(keys
    .filter((key) => key.startsWith("zurys-shell-") && key !== CACHE)
    .map((key) => caches.delete(key))
  )).then(() => self.clients.claim())
    .then(() => self.clients.matchAll({ type: "window", includeUncontrolled: true }))
    .then((windows) => Promise.all(windows.map((client) => client.navigate(client.url).catch(() => null))))
));

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/")));
    return;
  }
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (error) { data = {}; }
  const title = data.title || "Zurys 🌾";
  const options = {
    body: data.body || "",
    icon: data.icon || "/sedlak-cut.png",
    badge: "/sedlak-cut.png",
    tag: data.tag || "zurys",
    renotify: true,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let url = (event.notification.data && event.notification.data.url) || "/";
  if (url.charAt(0) === "#") url = "/" + url;
  event.waitUntil((async () => {
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if ("focus" in client) { try { await client.navigate(url); } catch (error) {} return client.focus(); }
    }
    return clients.openWindow(url);
  })());
});
