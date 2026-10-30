const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
function engineHarness({ save = async () => {}, requestWake } = {}) {
  const handlers = {}, events = [], messages = [], writes = [];
  let worker;
  const document = { visibilityState: 'visible', addEventListener(type, listener) { handlers[type] = listener; }, dispatchEvent(event) { events.push(event); } };
  const context = vm.createContext({ URL, structuredClone, console, setTimeout, clearTimeout, document,
    navigator: { userAgent: 'test browser', ...(requestWake ? { wakeLock: { request: requestWake } } : {}) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    stored: async (key, value) => { writes.push({ key, value }); await save(key, value); },
    Worker: class { constructor() { worker = this; } postMessage(data) { messages.push(data); } },
  });
  const source = fs.readFileSync('pwa/local-engine.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/local-engine.js'");
  vm.runInContext(source + '\nglobalThis.testApi = { runJob, request, jobs, files, canUpdate, get active() { return activeJob; } };', context);
  const api = context.testApi;
  const job = { id: 'job', videoId: 'video', video: { name: 'video.mp4', duration: 100, width: 100, height: 200, fps: 60 }, settings: { startTime: 3 }, moveCount: 2, time: 4 };
  api.jobs.set(job.id, job); api.files.set(job.videoId, {});
  return { api, job, events, messages, writes,
    start: () => api.runJob(job),
    visibility(state) { document.visibilityState = state; handlers.visibilitychange(); },
    respond(cancelled, count = 3) {
      const request = messages.filter(message => message.method === 'analyze').at(-1);
      worker.onmessage({ data: { id: request.id, value: { checkpoint: { lastTime: 5 }, nextSample: 5.1, result: { moves: Array.from({ length: count }, () => ({ usi: '2g2f' })), cancelled, lastTime: 5, analyzedFrames: 45 } } } });
    },
    checkpoint() { worker.onmessage({ data: { type: 'checkpoint', checkpoint: { lastTime: 4 }, result: { moves: [], lastTime: 4 } } }); },
  };
}

test('途中保存後に画面へ戻ると同じチェックポイントから一度だけ再開する', async () => {
  const app = engineHarness(); const running = app.start(); await flush();
  app.visibility('hidden'); app.respond(true); await running;
  assert.equal(app.job.status, 'cancelled');
  assert.equal(app.job.result.stopReason, 'visibility');
  assert.equal(app.job.moveCount, 3);
  app.visibility('visible'); app.visibility('visible'); await flush();
  const requests = app.messages.filter(message => message.method === 'analyze');
  assert.equal(requests.length, 2);
  assert.equal(requests[1].payload.checkpoint.lastTime, 5);
  assert.equal(requests[1].payload.nextSample, 5.1);
  assert.equal(app.events.filter(event => event.type === 'kifu-job-resumed').length, 1);
  app.respond(false, 20); await flush();
  assert.equal(app.job.status, 'complete'); assert.equal(app.job.moveCount, 20);
  assert.equal(app.job.result.diagnostics.status, 'complete');
});

test('停止完了前に画面へ戻っても保存とロック解除が済んでから再開する', async () => {
  const release = deferred(); let locks = 0;
  const app = engineHarness({ requestWake: async () => ({ addEventListener() {}, release: () => ++locks === 1 ? release.promise : Promise.resolve() }) });
  const running = app.start(); await flush();
  app.visibility('hidden'); app.visibility('visible'); app.respond(true); await flush();
  assert.equal(app.messages.filter(message => message.method === 'analyze').length, 1);
  release.resolve(); await running; await flush();
  assert.equal(app.messages.filter(message => message.method === 'analyze').length, 2);
  assert.equal(app.job.status, 'running');
  app.respond(false); await flush();
});

test('一時停止の保存中に手動停止したら自動再開せず停止理由も維持する', async () => {
  const saving = deferred();
  const app = engineHarness({ save: () => saving.promise });
  const running = app.start(); await flush();
  app.visibility('hidden'); app.respond(true); await flush();
  await app.api.request('/api/jobs/job/cancel'); app.visibility('visible'); saving.resolve(); await running;
  assert.equal(app.job.status, 'cancelled'); assert.equal(app.job.stopReason, 'user');
  assert.equal(app.job.result.stopReason, 'user');
  assert.equal(app.messages.filter(message => message.method === 'analyze').length, 1);
  assert.equal(app.writes.filter(write => write.key === 'job:job').at(-1).value.stopReason, 'user');
});

test('途中保存に失敗したジョブは自動再開せず保存エラーを表示する', async () => {
  const app = engineHarness({ save: async () => { throw new Error('保存容量が不足しています'); } });
  const running = app.start(); await flush();
  app.checkpoint(); await flush(); app.visibility('hidden'); app.respond(true); await running; app.visibility('visible'); await flush();
  assert.equal(app.job.status, 'failed'); assert.match(app.job.error, /保存容量/);
  assert.equal(app.messages.filter(message => message.method === 'analyze').length, 1);
  assert.ok(app.events.some(event => event.type === 'kifu-message' && event.detail.error));
});

test('ロック解除待ちの手動停止でも保存済みの停止理由を更新する', async () => {
  const release = deferred();
  const app = engineHarness({ requestWake: async () => ({ addEventListener() {}, release: () => release.promise }) });
  const running = app.start(); await flush();
  app.visibility('hidden'); app.respond(true); await flush();
  assert.equal(app.job.status, 'cancelled');
  assert.equal(app.api.canUpdate(), false);
  await app.api.request('/api/jobs/job/cancel');
  assert.equal(app.job.result.stopReason, 'user');
  assert.equal(app.writes.filter(write => write.key === 'job:job').at(-1).value.stopReason, 'user');
  app.visibility('visible'); release.resolve(); await running;
  assert.equal(app.messages.filter(message => message.method === 'analyze').length, 1);
  assert.equal(app.api.canUpdate(), true);
});

test('最後の進捗通知が古くても完了時の手数と時刻を結果に揃える', async () => {
  const app = engineHarness(); const running = app.start(); await flush(); app.respond(false, 20); await running;
  assert.equal(app.job.moveCount, 20); assert.equal(app.job.time, 5);
  assert.match(app.job.message, /20手/); assert.equal(app.job.result.stopReason, null);
  assert.equal(app.job.result.diagnostics.video.fps, 60);
});

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
    VideoDecoder: class {}, OffscreenCanvas: class { getContext() { return { drawImage() {}, getImageData: () => ({ data: new Uint8Array(72 * 72 * 4) }) }; } }, ALL_FORMATS: [], BlobSource: class {},
    Input: class { getPrimaryVideoTrack() { return { canDecode: async () => true }; } dispose() {} },
    CanvasSink: class { getCanvas() { return { canvas, timestamp: 0 }; } async *canvases() { yield { canvas, timestamp: .1 }; yield { canvas, timestamp: .2 }; throw new Error('decode interrupted'); } },
  });
  const source = fs.readFileSync('pwa/worker.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/worker.js'");
  vm.runInContext(source + '\nglobalThis.run = analyze;', context);
  await assert.rejects(context.run({ settings: { startTime: 0, sampleFps: 12, board: { x: 0, y: 0, width: 1, height: 1 } }, video: { duration: 5, fps: 30 } }), /decode interrupted/);
  const saved = messages.filter(message => message.type === 'checkpoint').at(-1);
  assert.equal(saved.checkpoint.lastTime, .2);
  assert.ok(Math.abs(saved.nextSample - (.2 + 1 / 12 - .5 / 30)) < 1e-10);
  assert.equal(saved.result.mediaDiagnostics.decodedFrames, 3);
  assert.equal(saved.result.mediaDiagnostics.firstFrameTime, 0);
  assert.equal(saved.result.mediaDiagnostics.lastDecodedTime, .2);
  assert.equal(saved.result.mediaDiagnostics.maxFrameGap, .1);
});

test('短い局面を予定時刻より前でも読み、変化後の確認フレームも省かない', () => {
  let probe = new Uint8Array(72 * 72 * 4).fill(220);
  const context = vm.createContext({ URL, Uint8Array, self: {},
    OffscreenCanvas: class { getContext() { return { drawImage() {}, getImageData: () => ({ data: new Uint8Array(probe) }) }; } },
  });
  const source = fs.readFileSync('pwa/worker.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/import\.meta\.url/g, "'https://example.github.io/kifu/worker.js'");
  vm.runInContext(source + '\nglobalThis.sampler = new MotionSampler();', context);
  const sample = () => context.sampler.shouldRead({ canvas: { width: 396, height: 438 } }, { x: 0, y: 0, width: 1, height: 1 }, false);
  for (let n = 0; n < 4; n++) assert.equal(sample(), true);
  assert.equal(sample(), false);
  for (let n = 0; n < 6; n++) {
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) for (let c = 0; c < 3; c++) probe[(y * 72 + x) * 4 + c] = n % 2 ? 220 : 20;
    assert.equal(sample(), true);
  }
  for (let n = 0; n < 3; n++) assert.equal(sample(), true);
  assert.equal(sample(), false);
});
