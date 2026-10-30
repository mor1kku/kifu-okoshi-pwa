const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flush = () => new Promise(resolve => setImmediate(resolve));

function updateHarness({ waiting, installing, allow = true, fakeTimers = false } = {}) {
  const events = [], handlers = {}, button = { disabled: false, addEventListener(type, action) { handlers[type] = action; } };
  let checks = 0, reloads = 0;
  const registration = { waiting, installing, update: async () => { checks++; } };
  const timers = new Map(); let timerId = 0;
  const context = vm.createContext({
    setTimeout: fakeTimers ? (action, delay) => { timers.set(++timerId, { action, delay }); return timerId; } : setTimeout,
    clearTimeout: fakeTimers ? id => timers.delete(id) : clearTimeout,
    document: { getElementById: () => button, dispatchEvent(event) { events.push(event); return event.type !== 'kifu-update-request' || allow; } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    navigator: { serviceWorker: { register: async (path, options) => { assert.equal(options.updateViaCache, 'none'); return registration; }, addEventListener: (type, action) => { handlers[type] = action; } } },
    location: { reload: () => reloads++ },
    MessageChannel: class { constructor() { this.port1 = { close() {} }; this.port2 = { postMessage: data => this.port1.onmessage({ data }) }; } },
  });
  vm.runInContext(fs.readFileSync('pwa/updates.js', 'utf8').replace(/\bexport /g, '') + '\nsetupUpdates();', context);
  return { events, handlers, registration, timers, click: () => handlers.click(), get checks() { return checks; }, get reloads() { return reloads; } };
}

test('更新の受付後に切替が完了しなくても操作を無期限にロックしない', async () => {
  const app = updateHarness({ fakeTimers: true, waiting: { postMessage(data, ports) { ports[0].postMessage({ done: true }); } } });
  await app.click();
  assert.equal(app.timers.size, 1);
  const timer = [...app.timers.values()][0]; assert.equal(timer.delay, 30000); timer.action();
  assert.ok(app.events.some(event => event.detail?.error && event.detail.message.includes('切替を確認できません')));
  assert.equal(app.events.at(-1).type, 'kifu-update-finished');
  app.handlers.controllerchange(); assert.equal(app.reloads, 0);
});

test('最新版でも解析結果の版は書き換えず、更新操作のロックを解除する', async () => {
  const app = updateHarness(); await app.click();
  assert.equal(app.checks, 1); assert.equal(app.reloads, 0);
  assert.ok(app.events.some(event => event.detail?.message.includes('再解析するまで変わりません')));
  assert.equal(app.events.at(-1).type, 'kifu-update-finished');
});

test('更新を拒否された操作や別タブの存在で画面を再読込しない', async () => {
  const busy = updateHarness({ allow: false }); await busy.click(); assert.equal(busy.checks, 0);
  const app = updateHarness({ waiting: { postMessage(data, ports) { ports[0].postMessage({ error: 'ほかのタブを閉じてください' }); } } });
  await app.click(); app.handlers.controllerchange();
  assert.equal(app.reloads, 0);
  assert.ok(app.events.some(event => event.detail?.error));
  assert.equal(app.events.at(-1).type, 'kifu-update-finished');
});

test('新版のダウンロード完了を待って適用し、切替時だけ再読み込みする', async () => {
  let stateListener, activations = 0;
  const waiting = { postMessage(data, ports) { assert.equal(data.type, 'activate-update'); activations++; ports[0].postMessage({ done: true }); } };
  const installing = { state: 'installing', addEventListener(type, listener) { stateListener = listener; }, removeEventListener() {} };
  const app = updateHarness({ installing });
  app.handlers.controllerchange(); assert.equal(app.reloads, 0);
  const applying = app.click(); await flush(); assert.equal(activations, 0);
  app.registration.waiting = waiting; installing.state = 'installed'; stateListener(); await applying;
  assert.equal(activations, 1); app.handlers.controllerchange(); assert.equal(app.reloads, 1);
});

test('別の棋譜おこし画面があるときはService Workerを切り替えない', async () => {
  const handlers = {}, replies = []; let activated = 0;
  let clients = [{ url: 'https://example.github.io/kifu/' }, { url: 'https://example.github.io/kifu/?tab=2' }];
  const context = vm.createContext({ URL,
    self: { registration: { scope: 'https://example.github.io/kifu/' }, addEventListener: (type, action) => { handlers[type] = action; },
      clients: { matchAll: async () => clients }, skipWaiting: async () => { activated++; } },
  });
  vm.runInContext(fs.readFileSync('pwa/sw.js', 'utf8'), context);
  const apply = async () => { let done; handlers.message({ data: { type: 'activate-update' }, ports: [{ postMessage: value => replies.push(value) }], waitUntil: promise => { done = promise; } }); await done; };
  await apply(); assert.equal(activated, 0); assert.match(replies.at(-1).error, /ほかの/);
  clients = [clients[0], { url: 'https://example.github.io/another/' }];
  await apply(); assert.equal(activated, 1); assert.equal(replies.at(-1).done, true);
});
