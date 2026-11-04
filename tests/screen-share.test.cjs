const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const modulePromise = import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync('pwa/screen-share-test.js', 'utf8')).toString('base64'));
const makeReport = () => ({ status: 'complete', intervals: [], environment: { standalone: false, userAgent: 'Test browser' } });

test('前面での更新や切替時の変化だけでは、別アプリ中に読み取れたと判定しない', async () => {
  const { changeVisibility, summarize } = await modulePromise;
  const report = makeReport();
  changeVisibility(report, 'visible', 0); report.intervals[0].changes = [500, 1000, 1500]; report.intervals[0].samples = 8;
  changeVisibility(report, 'hidden', 2000); report.intervals[1].changes = [2100, 2200, 2300, 2400, 21800, 21900];
  changeVisibility(report, 'visible', 22000);
  assert.equal(summarize(report, 23000).code, 'not-confirmed');
  assert.equal(summarize(report, 23000).samples, 8);
  assert.equal(report.intervals[1].end, 22000);
});

test('非表示中にタイマーが進んでも、映像が変化していなければ継続を確認できない', async () => {
  const { changeVisibility, summarize } = await modulePromise;
  const report = makeReport();
  changeVisibility(report, 'hidden', 1000); report.intervals[0].samples = 80;
  changeVisibility(report, 'visible', 21000);
  assert.equal(summarize(report).code, 'not-confirmed');
  assert.equal(summarize(report).maxGap, 16);
});

test('非表示の区間全体で映像変化が続いた場合だけ、試験中の継続を報告する', async () => {
  const { changeVisibility, summarize, resultText } = await modulePromise;
  const report = makeReport(); changeVisibility(report, 'hidden', 0);
  report.intervals[0].changes = Array.from({ length: 79 }, (_, index) => (index + 1) * 250);
  report.intervals[0].samples = 80; changeVisibility(report, 'visible', 20000);
  assert.equal(summarize(report).code, 'observed');
  assert.match(resultText(report), /棋譜解析はまだ未検証/);
  report.intervals[0].changes = report.intervals[0].changes.filter(time => time < 5000 || time > 14000);
  assert.equal(summarize(report).code, 'not-confirmed');
});

test('短い切替・未対応・許可キャンセルを区別する', async () => {
  const { changeVisibility, summarize } = await modulePromise;
  const report = makeReport(); changeVisibility(report, 'hidden', 0); changeVisibility(report, 'visible', 3000);
  assert.equal(summarize(report).code, 'untested');
  report.status = 'unsupported'; assert.equal(summarize(report).code, 'unsupported');
  report.status = 'error'; report.error = 'NotAllowedError'; assert.equal(summarize(report).code, 'error');
});

test('Service Workerは診断ページを棋譜画面に置き換えず、既存の画面遷移を維持する', async () => {
  const handlers = {}, keys = [];
  const scope = 'https://example.github.io/kifu/';
  const context = vm.createContext({ URL,
    self: { registration: { scope }, addEventListener: (type, listener) => { handlers[type] = listener; } },
    caches: { open: async () => ({ match: async key => { keys.push(typeof key === 'string' ? key : key.url); return { ok: true }; } }) },
  });
  vm.runInContext(fs.readFileSync('pwa/sw.js', 'utf8'), context);
  for (const [path, mode, expected] of [
    ['', 'navigate', 'index.html'], ['screen-share-test.html', 'navigate', 'screen-share-test.html'],
    ['screen-share-test.html?retry=1', 'navigate', 'screen-share-test.html'],
    ['screen-share-test.js', 'cors', 'screen-share-test.js'], ['old-link', 'navigate', 'index.html'],
  ]) {
    let done; handlers.fetch({ request: { method: 'GET', url: scope + path, mode }, respondWith: value => { done = value; } });
    await done; assert.equal(keys.at(-1), scope + expected);
  }
});
