const announce = (message, error = false) => document.dispatchEvent(new CustomEvent('kifu-message', { detail: { message, error } }));

async function waitForInstall(registration) {
  const installing = registration.installing;
  if (!installing) return registration.waiting;
  return new Promise((resolve, reject) => {
    const finish = () => {
      if (!['installed', 'redundant'].includes(installing.state)) return;
      clearTimeout(timer); installing.removeEventListener('statechange', finish);
      installing.state === 'installed' ? resolve(registration.waiting) : reject(new Error('新版のダウンロードに失敗しました。通信を確認して再試行してください。'));
    };
    const timer = setTimeout(() => { installing.removeEventListener('statechange', finish); reject(new Error('更新の準備が時間切れになりました。通信を確認して再試行してください。')); }, 180000);
    installing.addEventListener('statechange', finish); finish();
  });
}

export function setupUpdates() {
  const button = document.getElementById('updateButton');
  if (!('serviceWorker' in navigator)) { button.disabled = true; return; }
  const registrationPromise = navigator.serviceWorker.register('./sw.js', { scope: './', updateViaCache: 'none' });
  registrationPromise.catch(error => announce(`ホーム画面・オフラインの準備に失敗しました：${error.message}`, true));
  let applying = false, activationTimer;
  navigator.serviceWorker.addEventListener('controllerchange', () => { if (applying) { clearTimeout(activationTimer); location.reload(); } });
  button.addEventListener('click', async () => {
    // 解析・動画読込・未保存の棋譜修正があるときは画面を差し替えない。
    if (!document.dispatchEvent(new CustomEvent('kifu-update-request', { cancelable: true }))) return;
    button.disabled = true;
    try {
      announce('アプリの更新を確認しています。新版がある場合はダウンロードします…');
      const registration = await registrationPromise;
      await registration.update();
      const waiting = await waitForInstall(registration);
      if (!waiting) { announce('現在使える最新版です。保存済みの解析結果のバージョンは、再解析するまで変わりません。'); return; }
      announce('更新を適用して画面を開き直します。保存済みの棋譜は残ります。');
      applying = true;
      // 受付応答の後でも切替が失敗し得る。操作を無期限にロックしない。
      activationTimer = setTimeout(() => {
        applying = false;
        announce('更新の切替を確認できません。解析を始める前に画面を開き直してください。保存済みの棋譜は残っています。', true);
        document.dispatchEvent(new CustomEvent('kifu-update-finished'));
      }, 30000);
      await new Promise((resolve, reject) => {
        const channel = new MessageChannel();
        const timer = setTimeout(() => { channel.port1.close(); reject(new Error('更新の応答を確認できません。ほかのタブを閉じて再試行してください。')); }, 15000);
        channel.port1.onmessage = ({ data }) => {
          clearTimeout(timer); channel.port1.close();
          data.error ? reject(new Error(data.error)) : resolve();
        };
        waiting.postMessage({ type: 'activate-update' }, [channel.port2]);
      });
    } catch (error) { clearTimeout(activationTimer); applying = false; announce(error.message, true); }
    finally { if (!applying) document.dispatchEvent(new CustomEvent('kifu-update-finished')); }
  });
}
