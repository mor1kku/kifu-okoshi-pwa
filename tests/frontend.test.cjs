const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/app.js', 'utf8');
const response = value => ({ ok: true, status: 200, json: async () => value, blob: async () => ({}) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

function harness(fetch) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', children: [], dataset: {}, listeners: {}, classList: { toggle() {} }, addEventListener(name, fn) { this.listeners[name] = fn; }, append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; }, setAttribute() {}, getContext() { return new Proxy({}, { get: () => () => {} }); }, pause() {} });
    return elements.get(id);
  };
  const context = vm.createContext({ console, Blob, setTimeout, clearTimeout, FormData: class { append() {} },
    localStorage: { setItem() {}, getItem() { return null; } },
    document: { getElementById: element, createElement: tag => element(Symbol(tag)) },
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    Image: class { naturalWidth = 100; naturalHeight = 100; async decode() {} },
    fetch: url => url === '/api/sample' ? new Promise(() => {}) : fetch(url),
  });
  vm.runInContext(source + '\nglobalThis.appTest = { state, setVideo, setBusy, restoreSettings, pollJob, renderResult };', context);
  return { ...context.appTest, element };
}

test('遅れた動画・保存・比較の応答が現在の棋譜を上書きしない', async () => {
  const oldVideo = deferred(), oldSave = deferred(), oldCompare = deferred();
  const app = harness(async url => {
    if (url === '/api/videos/A/latest-result') return oldVideo.promise;
    if (url === '/api/videos/B/latest-result') return response({ id: 'job-B', moveCount: 2 });
    if (url === '/api/jobs/job-A/record') return oldSave.promise;
    if (url === '/api/jobs/job-B/compare') return oldCompare.promise;
    if (url.includes('/frame?')) return response({});
    throw Error(url);
  });
  const video = id => ({ id, name: id, duration: 100, width: 100, height: 100, fps: 30, size: 1000 });
  const loading = app.setVideo(video('A'));
  assert.equal(app.state.busy, true);
  await app.setVideo(video('B'));
  oldVideo.resolve(response({ id: 'job-A', moveCount: 100 })); await loading;
  assert.equal(app.state.savedJob, 'job-B');
  app.state.job = 'job-A'; app.state.result = { id: 'job-A', moves: [] };
  const saving = app.element('saveEdits').listeners.click();
  app.state.job = 'job-B'; app.state.result = { id: 'job-B', moves: [] };
  oldSave.resolve(response({ id: 'job-A', moves: [] })); await saving;
  assert.equal(app.state.result.id, 'job-B');
  const comparing = app.element('referenceFile').listeners.change({ target: { files: [{}], value: 'test' } });
  app.state.result = { id: 'job-B', moves: [{ usi: '2g2f' }] };
  oldCompare.resolve(response({ exactMatch: true, referenceCount: 1 })); await comparing;
  assert.equal(app.element('comparison').textContent, undefined);
});

test('解析設定の復元・再生中の開始位置・失われたジョブ', async () => {
  const app = harness(async () => ({ ok: false, status: 404, json: async () => ({ detail: '結果なし' }) }));
  app.restoreSettings({ board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'gote', sampleFps: 30, startTime: 10.6, endTime: 100 });
  assert.equal(app.element('sampleFps').value, 30);
  assert.equal(app.element('endTime').value, 100);
  app.state.player = true; app.state.frameTime = 10; app.element('player').currentTime = 25;
  app.element('useFrame').listeners.click();
  assert.equal(app.element('startTime').value, '25.00');
  app.state.job = 'missing'; app.setBusy(true); await app.pollJob();
  assert.equal(app.state.busy, false);
  assert.equal(app.element('cancelButton').disabled, true);
  app.state.job = 'new-result'; app.state.result = { moves: [], warnings: [] };
  app.renderResult();
  assert.equal(app.state.savedJob, 'new-result');
  assert.equal(app.element('savedResult').hidden, false);
});
