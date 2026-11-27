// Offline-first shell, network-first API (falls back to last good response for bad gym Wi-Fi).
const SHELL = "taper-shell-v10";
const API = "taper-api-v10";
const ASSETS = ["./", "index.html", "styles.css", "app.js", "vendor/vue.esm-browser.prod.js", "manifest.webmanifest",
  "fonts/barlow-latin-400-normal.woff2", "fonts/barlow-latin-500-normal.woff2", "fonts/barlow-latin-600-normal.woff2",
  "fonts/barlow-condensed-latin-500-normal.woff2", "fonts/barlow-condensed-latin-600-normal.woff2",
  "fonts/barlow-condensed-latin-700-normal.woff2", "icons/icon-192.png"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(API).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request)));
    return;
  }
  // app files: newest from the network first (so an update never mixes old CSS with new JS), cache when offline
  e.respondWith(fetch(e.request, { cache: "no-cache" }).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request)));
});
self.addEventListener("notificationclick", (e) => { e.notification.close(); e.waitUntil(self.clients.openWindow("./#/today")); });
