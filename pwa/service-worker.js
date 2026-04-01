const CACHE_NAME = 'goalforge-v10';
const SHELL_FILES = [
  '/',
  '/style.css',
  '/app.js',
  '/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Network-first for API calls
  if (url.pathname.startsWith('/api') ||
      url.pathname.startsWith('/goals') ||
      url.pathname.startsWith('/capture') ||
      url.pathname.startsWith('/chat') ||
      url.pathname.startsWith('/jobs') ||
      url.pathname.startsWith('/logs') ||
      url.pathname.startsWith('/config') ||
      url.pathname.startsWith('/inbox') ||
      url.pathname.startsWith('/categories') ||
      url.pathname.startsWith('/ideas') ||
      url.pathname.startsWith('/daily')) {
    event.respondWith(fetch(event.request).catch(() =>
      new Response(JSON.stringify({ error: 'Offline' }), {
        headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }

  // Cache-first for shell files
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
