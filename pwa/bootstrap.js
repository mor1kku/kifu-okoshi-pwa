import * as localEngine from './local-engine.js';
globalThis.kifuLocal = localEngine;
await import('./app.js');
const announce = (message, error = false) => document.dispatchEvent(new CustomEvent('kifu-message', { detail: { message, error } }));
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js', { scope: './' }).catch(error => announce(`ホーム画面・オフラインの準備に失敗しました：${error.message}`, true));
}
document.getElementById('offlineButton').addEventListener('click', async event => {
  event.target.disabled = true;
  try { await localEngine.prepareOffline(); announce('オフライン用の保存が完了しました。動画の原本もiPhoneに保存しておいてください。'); }
  catch (error) { announce(error.message, true); }
  finally { event.target.disabled = false; }
});
