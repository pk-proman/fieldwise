// App-shell cache so the PWA opens offline; API calls are never cached (the app queues writes itself).
const V = "fw-v14";
const SHELL = ["/", "/static/css/app.css", "/static/js/app.js", "/static/js/api.js", "/static/js/ui.js", "/static/js/tracker.js",
  "/static/js/pages-core.js", "/static/js/activate.js", "/static/js/pages-routehist.js", "/static/js/pages-exprules.js", "/static/js/pages-teamio.js", "/static/js/pages-crm.js", "/static/js/pages-hr.js", "/static/js/pages-admin.js", "/static/js/pages-sales.js", "/static/js/pages-orders.js", "/static/js/pages-beats.js", "/static/js/kit.js", "/static/js/pages-leads.js", "/static/js/pages-crm2.js", "/static/js/pages-ops.js", "/static/js/pages-settings2.js", "/static/js/pages-field.js", "/static/js/pages-settings3.js", "/static/js/brand.js", "/static/js/appupdate.js", "/static/js/pages-setup.js", "/static/js/pages-platform.js", "/static/js/trackwizard.js", "/static/js/native.js", "/static/icon.svg", "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/manifest.webmanifest"];
// No skipWaiting here: a new version waits until the page asks for it, so the user is never switched mid-task.
self.addEventListener("install", e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL).catch(() => {}))); });
self.addEventListener("message", e => { if (e.data === "skip-waiting") self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(k => Promise.all(k.filter(x => x !== V).map(x => caches.delete(x)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then(r => { if (r.ok && u.origin === location.origin) { const c = r.clone(); caches.open(V).then(x => x.put(e.request, c)); } return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match("/"))));
});
