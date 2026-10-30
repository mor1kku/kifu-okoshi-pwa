const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/app.js', 'utf8');
const response = value => ({ ok: true, status: 200, json: async () => value, blob: async () => ({}) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

function harness(fetch, localEngine) {
  const elements = new Map(), handlers = {};
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', children: [], dataset: {}, listeners: {}, classList: { toggle() {} }, addEventListener(name, fn) { this.listeners[name] = fn; }, append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; }, setAttribute() {}, getContext() { return new Proxy({}, { get: () => () => {} }); }, pause() {} });
    return elements.get(id);
  };
  const context = vm.createContext({ console, Blob, setTimeout, clearTimeout, FormData: class { append() {} },
    localStorage: { setItem() {}, getItem() { return null; } },
    document: { getElementById: element, createElement: tag => element(Symbol(tag)), addEventListener(type, listener) { handlers[type] = listener; } },
    kifuLocal: localEngine,
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    Image: class { naturalWidth = 100; naturalHeight = 100; async decode() {} },
    fetch: url => url === '/api/sample' ? new Promise(() => {}) : fetch(url),
  });
  vm.runInContext(source + '\nglobalThis.appTest = { state, setVideo, setBusy, restoreSettings, pollJob, renderResult, analysisSettings, selectGame, commitGame };', context);
  return { ...context.appTest, element, dispatch: (type, detail, preventDefault = () => {}) => handlers[type]({ detail, preventDefault }) };
}

test('解析中と未保存の棋譜がある間はアプリ更新で再読み込みしない', () => {
  let engineReady = true;
  const app = harness(null, { request: async () => new Promise(() => {}), canUpdate: () => engineReady });
  let refused = 0;
  app.setBusy(true); app.dispatch('kifu-update-request', null, () => refused++);
  assert.equal(refused, 1);
  app.setBusy(false); app.state.result = { moves: [], termination: null };
  app.element('editMoves').value = '7g7f'; app.element('termination').value = '';
  app.dispatch('kifu-update-request', null, () => refused++); assert.equal(refused, 2);
  app.element('editMoves').value = '';
  engineReady = false; app.dispatch('kifu-update-request', null, () => refused++); assert.equal(refused, 3);
  engineReady = true;
  app.dispatch('kifu-update-request', null, () => refused++); assert.equal(refused, 3); assert.equal(app.state.busy, true);
  app.dispatch('kifu-update-finished'); assert.equal(app.state.busy, false);
});

test('結果の手数を完了欄に使い、自動再開時には進捗の取得を再開する', async () => {
  let reads = 0;
  const settings = { board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'gote', sampleFps: 12 };
  const result = { moves: [], settings, cancelled: true, stopReason: 'visibility' };
  const engine = {
    request: async path => {
      if (path === '/api/sample') return new Promise(() => {});
      reads++; return { status: 'cancelled', stopReason: 'visibility', moveCount: 2, progress: .1, result };
    },
    storageKey: key => key, kifLink: () => 'blob:kif', canResume: () => true,
  };
  const app = harness(null, engine);
  app.state.video = { id: 'video' }; app.state.job = 'job';
  await app.pollJob();
  assert.match(app.element('progressDetail').textContent, /0手/);
  assert.match(app.element('resultSummary').textContent, /途中結果：0手.*一時停止/);
  assert.equal(app.state.busy, false);
  app.dispatch('kifu-job-resumed', { id: 'another-job', videoId: 'video' });
  assert.equal(reads, 1);
  app.dispatch('kifu-job-resumed', { id: 'job', videoId: 'video' });
  assert.equal(app.state.busy, true);
  assert.equal(app.element('resultBody').hidden, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reads, 2);
});

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

test('同じ動画の開始位置を保ち、別動画には前の盤面設定を流用しない', async () => {
  const app = harness(async url => response(url.endsWith('/latest-result') ? { id: null } : {}));
  const video = id => ({ id, name: id, duration: 100, width: 100, height: 100, fps: 30, size: 1000 });
  await app.setVideo(video('A'));
  const settings = { profile: 'wars', orientation: 'gote', sampleFps: 60, startTime: 3, endTime: 80, board: { x: .1, y: .2, width: .8, height: .5 } };
  app.restoreSettings(settings);
  await app.setVideo(video('A'));
  assert.deepEqual(JSON.parse(JSON.stringify(app.analysisSettings())), { ...settings, videoId: 'A' });
  await app.setVideo(video('B'));
  const fresh = app.analysisSettings();
  assert.equal(fresh.startTime, 0); assert.equal(fresh.endTime, null);
  assert.equal(fresh.profile, 'kioh'); assert.equal(fresh.orientation, 'sente'); assert.equal(fresh.sampleFps, 12);
  assert.notEqual(fresh.board.x, settings.board.x);
});

test('動画用設定JSONの開始時刻をプレビューと解析に適用する', async () => {
  const app = harness(async url => response(url.endsWith('/latest-result') ? { id: null } : {}));
  await app.setVideo({ id: 'A', name: 'A', duration: 10, width: 100, height: 100, fps: 60, size: 1000 });
  const profile = { version: 1, profile: 'wars', orientation: 'gote', sampleFps: 12, startTime: 3, board: { x: 0, y: 0, width: 1, height: 1 } };
  await app.element('profileFile').listeners.change({ target: { files: [{ size: 1000, text: async () => JSON.stringify(profile) }], value: 'settings' } });
  assert.equal(app.analysisSettings().startTime, 3);
  assert.equal(app.analysisSettings().orientation, 'gote');
  assert.equal(app.state.frameTime, 3);
  assert.match(app.element('notice').textContent, /開始時刻も適用/);
});

test('局の設定変更と切替は他局の先後・盤枠・棋譜を変更しない', async () => {
  const engine = { getSession: async () => ({}), saveSession: async session => structuredClone(session),
    request: async () => new Promise(() => {}), frameBlob: async () => ({}), storageKey: key => key };
  const app = harness(null, engine); app.state.video = { id: 'video', duration: 60 };
  const settings = { videoId: 'video', startTime: 0, endTime: 20, board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'sente', sampleFps: 12 };
  app.state.session = { videoId: 'video', queue: { status: 'idle' }, games: [
    { id: 'a', settings: structuredClone(settings), jobId: null },
    { id: 'b', settings: { ...structuredClone(settings), startTime: 30, endTime: 50, orientation: 'gote' }, jobId: null },
  ] };
  await app.selectGame('a', false); app.state.box = { x: .1, y: .1, width: .8, height: .8 }; app.element('endTime').value = '18';
  await app.selectGame('b'); assert.equal(app.element('orientation').value, 'gote'); assert.equal(app.state.box.x, 0);
  await app.selectGame('a'); assert.equal(app.state.box.x, .1); assert.equal(app.element('endTime').value, 18);
  app.state.session.games[0].jobId = 'existing';
  await app.commitGame(); assert.equal(app.state.session.games[0].jobId, 'existing');
  app.element('orientation').value = 'gote'; await app.commitGame(); assert.equal(app.state.session.games[0].jobId, null);
});

test('局切替後に遅れた旧局の結果が届いても選択局を上書きしない', async () => {
  const late = deferred();
  const settings = { videoId: 'video', startTime: 0, endTime: null, sampleFps: 12, board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'gote' };
  const engine = { getSession: async () => ({}), saveSession: async session => structuredClone(session),
    request: async path => path === '/api/jobs/old' ? late.promise : new Promise(() => {}), frameBlob: async () => ({}), storageKey: key => key };
  const app = harness(null, engine); app.state.video = { id: 'video', duration: 60 };
  app.state.session = { videoId: 'video', queue: { status: 'idle' }, games: [{ id: 'a', jobId: 'old', settings }, { id: 'b', settings }] };
  const selecting = app.selectGame('a', false); await app.selectGame('b', false);
  late.resolve({ result: { moves: [], settings } }); await selecting;
  assert.equal(app.state.gameId, 'b'); assert.equal(app.state.result, null);
});

test('局設定の保存中に別動画へ移っても旧応答で動画と設定を混ぜない', async () => {
  const saving = deferred();
  const engine = { getSession: async () => ({}), saveSession: () => saving.promise,
    request: async () => new Promise(() => {}), frameBlob: async () => ({}), storageKey: key => key };
  const app = harness(null, engine); app.state.video = { id: 'A', duration: 60 };
  const settings = { videoId: 'A', startTime: 0, endTime: 20, sampleFps: 12, board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'sente' };
  const old = { videoId: 'A', queue: { status: 'idle' }, games: [{ id: 'a1', settings }, { id: 'a2', settings: { ...settings, startTime: 30, endTime: 50 } }] };
  app.state.session = old; await app.selectGame('a1', false);
  const selecting = app.selectGame('a2');
  app.state.video = { id: 'B', duration: 60 }; app.state.videoVersion++;
  app.state.session = { videoId: 'B', queue: { status: 'idle' }, games: [] }; app.state.gameId = null;
  saving.resolve(structuredClone(old)); await selecting;
  assert.equal(app.state.video.id, 'B'); assert.equal(app.state.session.videoId, 'B'); assert.equal(app.state.gameId, null);
});

test('局設定の保存エラーでは保存済み棋譜との関連を失わない', async () => {
  const settings = { videoId: 'video', startTime: 0, endTime: 20, sampleFps: 12, board: { x: 0, y: 0, width: 1, height: 1 }, profile: 'wars', orientation: 'sente' };
  const engine = { getSession: async () => ({}), saveSession: async () => { throw new Error('保存失敗'); }, request: async () => new Promise(() => {}) };
  const app = harness(null, engine); app.state.video = { id: 'video', duration: 60 };
  app.state.gameId = 'a'; app.state.job = 'record'; app.state.result = { moves: [] };
  app.state.session = { videoId: 'video', queue: { status: 'idle' }, games: [{ id: 'a', jobId: 'record', settings }] };
  app.restoreSettings(settings); app.element('orientation').value = 'gote';
  await assert.rejects(app.commitGame(), /保存失敗/);
  assert.equal(app.state.session.games[0].jobId, 'record'); assert.equal(app.state.job, 'record');
  assert.equal(app.state.session.games[0].settings.orientation, 'sente'); assert.ok(app.state.result);
});
