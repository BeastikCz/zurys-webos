/* Zurys service worker — Web Push: zobrazí notifikaci (i když je appka zavřená) a po kliknutí
   otevře / zaměří appku na správné stránce (např. #/zahrada u chrobáků). */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = {}; }
  const title = d.title || "Zurys 🌾";
  const opts = {
    body: d.body || "",
    icon: d.icon || "/sedlak-cut.png",
    badge: "/sedlak-cut.png",
    tag: d.tag || "zurys",
    renotify: true,
    data: { url: d.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let url = (event.notification.data && event.notification.data.url) || "/";
  if (url.charAt(0) === "#") url = "/" + url;   // hash route → /#/zahrada
  event.waitUntil((async () => {
    const wins = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) { try { await w.navigate(url); } catch (e) {} return w.focus(); }
    }
    return clients.openWindow(url);
  })());
});
