const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('新版の保存に旧HTTPキャッシュを混ぜず、エンジンと取得済みの一覧を保存する', async () => {
  const handlers = {}; let cached, savedManifest; const freshManifest = {};
  const context = vm.createContext({ URL, Request,
    self: { registration: { scope: 'https://example.github.io/kifu/' }, addEventListener: (type, action) => { handlers[type] = action; } },
    fetch: async () => ({ ok: true, clone: () => freshManifest, json: async () => ['python.zip', 'vendor/pyodide/pyodide.asm.wasm', 'vendor/pyodide/opencv.whl'] }),
    caches: { open: async () => ({ addAll: async requests => { cached = requests; }, put: async (url, response) => { savedManifest = { url, response }; } }) },
  });
  vm.runInContext(fs.readFileSync('pwa/sw.js', 'utf8'), context);
  let completed; handlers.install({ waitUntil(promise) { completed = promise; } }); await completed;
  for (const path of ['python.zip', 'vendor/pyodide/pyodide.asm.wasm', 'vendor/pyodide/opencv.whl']) assert.ok(cached.some(request => request.url === 'https://example.github.io/kifu/' + path));
  assert.ok(cached.every(request => request.cache === 'reload'));
  assert.ok(cached.every(request => !request.url.endsWith('/asset-manifest.json')));
  assert.equal(savedManifest.url, 'https://example.github.io/kifu/asset-manifest.json');
  assert.equal(savedManifest.response, freshManifest);
});

test('PWA更新が別のGitHub Pagesアプリのキャッシュを消さない', async () => {
  const handlers = {}, removed = [];
  const context = vm.createContext({ URL,
    self: { registration: { scope: 'https://example.github.io/kifu/' }, clients: { claim: async () => {} }, addEventListener: (type, action) => { handlers[type] = action; } },
    caches: { keys: async () => ['kifu-pwa:/kifu/:old', 'kifu-pwa:/another/:old', 'unrelated'], delete: async name => removed.push(name) },
  });
  vm.runInContext(fs.readFileSync('pwa/sw.js', 'utf8'), context);
  let completed; handlers.activate({ waitUntil(promise) { completed = promise; } }); await completed;
  assert.deepEqual(removed, ['kifu-pwa:/kifu/:old']);
});

test('画面ロック防止の準備中に停止しても停止要求を失わない', async () => {
  let releaseWake, instance;
  const wake = new Promise(resolve => { releaseWake = resolve; });
  const messages = [];
  const context = vm.createContext({ URL, structuredClone, console, setTimeout, clearTimeout,
    document: { visibilityState: 'visible', addEventListener() {}, dispatchEvent() {} },
    navigator: { wakeLock: { request: () => wake } },
    CustomEvent: class {}, stored: async () => {},
    Worker: class { constructor() { instance = this; } postMessage(data) {
      messages.push(data);
      if (data.method === 'analyze') queueMicrotask(() => instance.onmessage({ data: { id: data.id, value: { result: { moves: [], cancelled: true }, checkpoint: {} } } }));
    } },
  });
  const source = fs.readFileSync('pwa/local-engine.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/local-engine.js'");
  vm.runInContext(source + '\nglobalThis.testApi = { runJob, request, jobs, files };', context);
  const { runJob, request, jobs, files } = context.testApi;
  const job = { id: 'job', videoId: 'video', video: { name: 'video.mp4' }, settings: {} };
  jobs.set('job', job); files.set('video', {});
  const running = runJob(job);
  await request('/api/jobs/job/cancel');
  releaseWake({ addEventListener() {}, release: async () => {} });
  await running;
  assert.equal(messages.find(message => message.method === 'analyze').payload.cancelRequested, true);
  assert.equal(job.status, 'cancelled');
});

test('同じ動画を名前や更新日が変わって再選択しても識別できる', async () => {
  const { webcrypto } = require('node:crypto');
  const source = fs.readFileSync('pwa/storage.js', 'utf8').replace(/\bexport /g, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/storage.js'");
  const context = vm.createContext({ URL, TextEncoder, Uint8Array, crypto: webcrypto });
  vm.runInContext(source + '\nglobalThis.getId = fileId;', context);
  const bytes = Buffer.alloc(250000, 17);
  const first = new File([bytes], 'first.mp4', { lastModified: 1 });
  const second = new File([bytes], 'renamed.mp4', { lastModified: 2 });
  assert.equal(await context.getId(first), await context.getId(second));
  bytes[125000] = 42;
  assert.notEqual(await context.getId(first), await context.getId(new File([bytes], 'first.mp4')));
});

test('デコード中断時も次に読む時刻をチェックポイントへ保存する', async () => {
  const messages = []; let time = 0;
  const fakeBridge = (method, payload) => {
    const value = JSON.parse(payload);
    if (method === 'frame') time = value.time;
    return JSON.stringify(method === 'snapshot' ? { result: { moves: [] }, checkpoint: { lastTime: time } } : { moveCount: 0 });
  };
  const canvas = { width: 10, height: 10, getContext: () => ({ getImageData: () => ({ data: new Uint8Array(400) }) }) };
  const context = vm.createContext({ URL, Uint8Array, setTimeout, performance: { now: () => 0 },
    self: { postMessage: value => messages.push(value) },
    loadPyodide: async () => ({ loadPackage: async () => {}, unpackArchive() {}, runPython() {}, globals: { get: () => fakeBridge } }),
    fetch: async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) }),
    VideoDecoder: class {}, OffscreenCanvas: class {}, ALL_FORMATS: [], BlobSource: class {},
    Input: class { getPrimaryVideoTrack() { return { canDecode: async () => true }; } dispose() {} },
    CanvasSink: class { getCanvas() { return { canvas, timestamp: 0 }; } async *canvases() { yield { canvas, timestamp: .1 }; yield { canvas, timestamp: .2 }; throw new Error('decode interrupted'); } },
  });
  const source = fs.readFileSync('pwa/worker.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/worker.js'");
  vm.runInContext(source + '\nglobalThis.run = analyze;', context);
  await assert.rejects(context.run({ settings: { startTime: 0, sampleFps: 12, board: { x: 0, y: 0, width: 1, height: 1 } }, video: { duration: 5, fps: 30 } }), /decode interrupted/);
  const saved = messages.filter(message => message.type === 'checkpoint').at(-1);
  assert.equal(saved.checkpoint.lastTime, .2);
  assert.ok(Math.abs(saved.nextSample - (.2 + 1 / 12 - .5 / 30)) < 1e-10);
});
