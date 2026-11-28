// Service Worker — 静态资源缓存 + API Network First
const CACHE_NAME = 'stock-scanner-v3';
const STATIC_ASSETS = [
  '/',
  '/css/main.css',
  '/css/layout.css',
  '/css/apple-tokens.css',
  '/css/apple-components.css',
  '/css/e-layout.css',
  '/css/accessibility.css',
  '/js/main.js',
  '/js/api.js',
  '/js/state.js',
  '/js/websocket.js',
  '/js/polling.js',
  '/js/theme.js',
  '/js/notifications.js',
  '/js/error_tracker.js',
  '/js/onboarding.js',
  '/js/lib/chart.umd.min.js',
  '/js/components/Sidebar.js',
  '/js/components/Toolbar.js',
  '/js/components/StatusBar.js',
  '/js/layouts/AppLayout.js',
  '/js/renderers/utils.js',
  '/js/renderers/metrics.js',
  '/js/renderers/portfolio.js',
  '/js/renderers/scanner.js',
  '/js/renderers/risk.js',
  '/js/renderers/positions.js',
  '/js/renderers/exposures.js',
  '/js/renderers/audit.js',
  '/js/renderers/backtest.js',
  '/js/renderers/eval.js',
  '/js/renderers/signals.js',
  '/js/renderers/trades.js',
  '/js/renderers/settings.js',
  '/favicon.svg',
];

// Install: 预缓存静态资源
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate: 清理旧缓存
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

// Fetch: 静态资源 Cache First, API Network Only（不缓存认证数据）
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 只处理同源请求
  if (url.origin !== location.origin) return;

  // API/WebSocket 请求: Network Only（不缓存认证数据）
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws') {
    event.respondWith(fetch(event.request));
    return;
  }

  // 静态资源: Cache First
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        if (response.ok) {
          const cloned = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, cloned);
          });
        }
        return response;
      });
    })
  );
});
