const base = self.registration.scope;
const cachePrefix = `kifu-pwa:${new URL(base).pathname}:`;
const cacheName = `${cachePrefix}__BUILD_ID__`;
const url = path => new URL(path, base).href;
const shell = ['index.html', 'style.css', 'app.js', 'bootstrap.js', 'local-engine.js', 'storage.js', 'worker.js', 'profiles.json', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png', 'asset-manifest.json'];
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const response = await fetch(url('asset-manifest.json'), { cache: 'no-store' });
    if (!response.ok) throw new Error('配布物の一覧を取得できません。');
    const manifestResponse = response.clone();
    const assets = await response.json();
    const cache = await caches.open(cacheName);
    // エンジンも揃うまで新版を有効にしない。更新直後のオフライン起動を保つ。
    const paths = [...new Set([...shell, ...assets])].filter(path => path !== 'asset-manifest.json');
    // HTTPキャッシュの旧版が、新版の保存一覧やスクリプトに混ざることを防ぐ。
    await cache.addAll(paths.map(path => new Request(url(path), { cache: 'reload' })));
    await cache.put(url('asset-manifest.json'), manifestResponse);
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    // 更新は次の起動で適用し、解析中の古い画面を差し替えない。
    for (const name of await caches.keys()) if (name.startsWith(cachePrefix) && name !== cacheName) await caches.delete(name);
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || !event.request.url.startsWith(base)) return;
  event.respondWith((async () => {
    const key = event.request.mode === 'navigate' ? url('index.html') : event.request;
    const cache = await caches.open(cacheName);
    const cached = await cache.match(key); if (cached) return cached;
    const response = await fetch(event.request);
    if (response.ok) {
      // 配布物だけを保存する。動画や結果をネットワークキャッシュへ入れない。
      const relative = event.request.url.slice(base.length);
      if (shell.includes(relative) || relative.startsWith('vendor/') || relative === 'python.zip') {
        try { await cache.put(key, response.clone()); } catch { /* 保存容量不足でもオンライン解析は継続できる。 */ }
      }
    }
    return response;
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'prepare-offline') return;
  const port = event.ports[0];
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(cacheName);
      const manifest = await (await cache.match(url('asset-manifest.json'))).json();
      let completed = 0;
      for (const path of manifest) {
        if (!await cache.match(url(path))) {
          const response = await fetch(url(path));
          if (!response.ok) throw new Error(`${path} を取得できません。`);
          await cache.put(url(path), response);
        }
        port.postMessage({ completed: ++completed, total: manifest.length });
      }
      port.postMessage({ done: true });
    } catch (error) { port.postMessage({ error: `オフライン保存に失敗しました：${error.message}` }); }
  })());
});
