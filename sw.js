// Chore Tracker Service Worker — cache-first for offline support
const CACHE_NAME = 'chore-tracker-v5';
const ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './fonts/poppins-400.woff2',
  './fonts/poppins-600.woff2',
  './fonts/poppins-700.woff2',
  './icons/icon-16.png',
  './icons/icon-32.png',
  './icons/icon-192.png',
  './icons/icon-512.png'
];

// Install: pre-cache shell assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: network-first for HTML (so updates are picked up), cache-first for other assets
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isNav = event.request.mode === 'navigate';
  const isHTML = url.pathname.endsWith('.html') || url.pathname === '/' || url.pathname === '';
  if (isNav || isHTML) {
    // Network-first for HTML — always get the latest, fall back to cache offline
    event.respondWith(
      fetch(event.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
  } else {
    // Cache-first for fonts, icons, manifest etc.
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
  }
});
