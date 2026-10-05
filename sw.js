// 홈 화면 앱용: 늘 새로 받아 오고, 인터넷이 끊겼을 때만 마지막으로 본 화면을 보여 준다
const CACHE = 'ak-board-v1';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  e.respondWith(
    fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return res;
    }).catch(() => caches.match(req, {ignoreSearch: true}))
  );
});
