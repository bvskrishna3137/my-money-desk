/* My Money Desk: service worker.
   Keeps a copy of the app files so the app still opens with no network or with the
   save server switched off (the app then works from the browser's own storage).
   It never touches /api/*, so your saved data always comes straight from the server.
   Strategy: network first, fall back to the cached copy. */
const CACHE = 'mmd-shell-v1';
const SHELL = ['/', '/index.html', '/native.js', '/manifest.json', '/icon.svg', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  const key = new Request(url.origin + url.pathname); /* ignore ?v=... when caching */
  e.respondWith(
    fetch(req)
      .then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
        return res;
      })
      .catch(() => caches.match(key).then(hit => hit || Response.error()))
  );
});
