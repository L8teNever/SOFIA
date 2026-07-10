// Cache version is injected at serve-time — changes on every new deployment
const CACHE = 'sofia-v__BUILD__';
const PRECACHE = [
  '/',
  '/static/css/base.css',
  '/static/css/components.css',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/notifications.js',
  '/static/manifest.json',
];

self.addEventListener('install', e => {
  // Pre-cache core assets; do NOT skipWaiting — let the page decide when to activate
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)));
});

self.addEventListener('activate', e => {
  // Delete all old caches (different version name = old deployment)
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => {
      self.clients.claim();
      // Notify all open tabs that a new version has taken over
      self.clients.matchAll({ type: 'window' }).then(clients => {
        clients.forEach(c => c.postMessage({ type: 'SW_UPDATED' }));
      });
    })
  );
});

// Allow the page to trigger skipWaiting (used by the update toast "Neu laden" button)
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  // Never cache: API calls, page templates, favicon, or the SW itself
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/pages/') ||
    url.pathname === '/favicon.ico' ||
    url.pathname === '/sw.js'
  ) return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      // Network-first for HTML root, cache-first for everything else
      const isHtml = url.pathname === '/';
      if (isHtml) {
        return fetch(e.request)
          .then(res => {
            if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
            return res;
          })
          .catch(() => cached);
      }
      const fresh = fetch(e.request).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      });
      return cached || fresh;
    })
  );
});

self.addEventListener('push', e => {
  const data = e.data?.json() || {};
  e.waitUntil(self.registration.showNotification(data.title || 'Sofia', {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    vibrate: [100, 50, 100],
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data?.url || '/'));
});
