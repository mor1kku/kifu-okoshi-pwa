const reportKey = 'kifu-screen-share-test-v1';

export function changeVisibility(report, visibility, time) {
  const current = report.intervals.at(-1);
  if (current?.visibility === visibility && current.end == null) return;
  if (current && current.end == null) current.end = time;
  report.intervals.push({ visibility, start: time, end: null, samples: 0, changes: [] });
}

export function summarize(report, now = Date.now()) {
  const intervals = report.intervals ?? [];
  const hidden = intervals.filter(part => part.visibility === 'hidden');
  const tested = hidden.filter(part => (part.end ?? now) - part.start >= 10000)
    .sort((a, b) => ((b.end ?? now) - b.start) - ((a.end ?? now) - a.start))[0];
  const summary = { code: 'untested', samples: intervals.reduce((sum, part) => sum + part.samples, 0),
    hiddenChanges: hidden.reduce((sum, part) => sum + part.changes.length, 0), seconds: 0, maxGap: null };
  if (report.status === 'unsupported') return { ...summary, code: 'unsupported' };
  if (report.status === 'error' && !tested) return { ...summary, code: 'error' };
  if (!tested) return summary;
  // 切替直後・復帰直前の映像だけで、継続できたと判定しない。
  const start = tested.start + 2000, end = (tested.end ?? now) - 2000;
  const changes = tested.changes.filter(time => time >= start && time <= end);
  const points = [start, ...changes, end];
  summary.maxGap = Math.max(...points.slice(1).map((time, index) => time - points[index])) / 1000;
  summary.seconds = ((tested.end ?? now) - tested.start) / 1000;
  summary.code = changes.length >= 8 && summary.maxGap <= 3 ? 'observed' : 'not-confirmed';
  return summary;
}

export function resultText(report, now = Date.now()) {
  const summary = summarize(report, now);
  const messages = {
    untested: '未確認：別アプリで動きのある動画を20秒ほど見てから戻ってください。',
    unsupported: 'この起動方法では、画面共有の機能を利用できません。Safariで開いた場合も同じか確認してください。',
    error: '画面共有を開始できませんでした。許可のキャンセルや、ブラウザー側の制限などが考えられます。',
    observed: '試験中、このページが非表示の間にも共有映像の変化を読み取れました。棋譜解析はまだ未検証です。',
    'not-confirmed': '別アプリ中に映像を連続して読み取れたことは確認できませんでした。ページの休止、共有映像の停止、動画が静止していた可能性があります。',
  };
  const surface = report.capture?.displaySurface;
  const surfaceName = { monitor: '画面全体', window: 'ウインドウ', browser: 'ブラウザーのタブ' }[surface] ?? '情報なし';
  return [messages[summary.code], `状態：${report.status}`, `ページ非表示の確認区間：${summary.seconds.toFixed(1)}秒`,
    `映像読取：${summary.samples}回 / 別アプリ中の変化：${summary.hiddenChanges}回`,
    summary.maxGap == null ? '' : `映像変化の最大間隔：${summary.maxGap.toFixed(1)}秒`,
    `共有元：${surfaceName}${surface === 'browser' ? '（別アプリの画面の取り込みは未確認）' : ''}`,
    report.error ? `エラー：${report.error}` : '', `起動：${report.environment.standalone ? 'ホーム画面版' : 'ブラウザー'}`,
    `ブラウザー：${report.environment.userAgent}`].filter(Boolean).join('\n');
}

if (typeof document !== 'undefined') mount();

function mount() {
  const $ = id => document.getElementById(id);
  const video = $('capturePreview'), canvas = document.createElement('canvas');
  canvas.width = 96; canvas.height = 54;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  let report, stream, timer, active = false, pending = false, generation = 0, previousHash, lastSaved = 0;
  const available = isSecureContext && typeof navigator.mediaDevices?.getDisplayMedia === 'function';
  const environment = { userAgent: navigator.userAgent, standalone: Boolean(navigator.standalone || matchMedia('(display-mode: standalone)').matches), secure: isSecureContext, screenCapture: available };
  const save = () => {
    if (!report) return;
    report.lastSavedAt = Date.now();
    try { localStorage.setItem(reportKey, JSON.stringify(report)); }
    catch { $('saveNote').textContent = '端末に診断結果を保存できません。「結果をコピー」または「JSONを保存」を使ってください。'; }
  };
  const render = () => {
    if (!report) return;
    const summary = summarize(report);
    $('sampleCount').textContent = summary.samples;
    $('hiddenChanges').textContent = summary.hiddenChanges;
    $('resultText').textContent = resultText(report);
    $('copyResult').disabled = $('saveResult').disabled = false;
  };
  function stop(status = 'complete') {
    ++generation; active = pending = false; clearInterval(timer);
    if (report) {
      const last = report.intervals.at(-1); if (last && last.end == null) last.end = Date.now();
      report.status = status; report.endedAt = Date.now();
    }
    const currentStream = stream; stream = null;
    currentStream?.getTracks().forEach(track => track.stop());
    video.pause(); video.srcObject = null; video.hidden = true;
    context.clearRect(0, 0, canvas.width, canvas.height);
    $('startCapture').disabled = !available; $('stopCapture').disabled = true;
    $('status').textContent = status === 'error' ? '診断結果に開始できなかった理由を記録しました。' : '画面共有を終了しました。結果をコピーしてチャットへ貼り付けてください。';
    save(); render();
  }
  function sample() {
    if (!active) return;
    const time = Date.now();
    // 非表示中も停止せず、実際に読み取れた時刻だけを記録する。
    changeVisibility(report, document.visibilityState, time);
    if (time - report.startedAt >= 120000) { stop(); return; }
    if (video.readyState >= 2 && video.videoWidth) {
      try {
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let hash = 2166136261;
        for (let index = 0; index < pixels.length; index += 4) {
          hash = Math.imul(hash ^ pixels[index], 16777619);
          hash = Math.imul(hash ^ pixels[index + 1], 16777619);
          hash = Math.imul(hash ^ pixels[index + 2], 16777619);
        }
        const interval = report.intervals.at(-1); interval.samples++;
        if (previousHash !== undefined && hash !== previousHash) interval.changes.push(time);
        previousHash = hash;
      } catch (error) { report.error = error.name; stop('error'); return; }
    }
    if (time - lastSaved >= 2000) { save(); lastSaved = time; }
    if (document.visibilityState === 'visible') render();
  }
  $('startCapture').addEventListener('click', async () => {
    if (!available || active || pending) return;
    const request = ++generation;
    report = { version: 1, createdAt: Date.now(), status: 'requesting', environment, intervals: [] };
    pending = true; previousHash = undefined; $('startCapture').disabled = true; $('stopCapture').disabled = false;
    $('saveNote').textContent = ''; $('status').textContent = '画面共有の許可を待っています…'; save(); render();
    try {
      // ユーザー操作の中で直接呼び、音声は要求しない。
      const acquired = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      if (request !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      const track = stream.getVideoTracks()[0];
      if (!track) throw new DOMException('映像がありません', 'NotFoundError');
      const settings = track.getSettings();
      report.capture = { width: settings.width, height: settings.height, frameRate: settings.frameRate, displaySurface: settings.displaySurface };
      track.addEventListener('ended', () => { if (request === generation) stop('ended'); });
      video.srcObject = stream; video.hidden = false; await video.play();
      if (request !== generation) return;
      pending = false; active = true; report.status = 'running'; report.startedAt = Date.now();
      changeVisibility(report, document.visibilityState, report.startedAt);
      $('status').textContent = '共有中です。動画のアプリに切り替え、20秒ほど再生してから戻ってください。';
      save(); sample(); timer = setInterval(sample, 250);
    } catch (error) {
      if (request !== generation) return;
      report.error = error.name; stop('error');
    }
  });
  $('stopCapture').addEventListener('click', () => stop());
  document.addEventListener('visibilitychange', () => {
    if (active) { changeVisibility(report, document.visibilityState, Date.now()); save(); render(); }
  });
  addEventListener('pagehide', () => { if (active || pending) stop('interrupted'); });
  $('copyResult').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(resultText(report)); $('saveNote').textContent = 'コピーしました。チャットに貼り付けてください。'; }
    catch { $('saveNote').textContent = 'コピーできません。表示された結果を選択してコピーするか、「JSONを保存」を使ってください。'; }
  });
  $('saveResult').addEventListener('click', () => {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    link.download = 'screen-share-test.json'; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 30000);
  });
  try {
    const saved = JSON.parse(localStorage.getItem(reportKey));
    if (saved?.version === 1 && Array.isArray(saved.intervals) && saved.environment) {
      report = saved;
      if (['running', 'requesting'].includes(report.status)) {
        report.status = 'interrupted';
        // 休止後の再読み込みまで、観測していたことにはしない。
        const last = report.intervals.at(-1);
        if (last && last.end == null) last.end = report.lastSavedAt ?? last.start;
      }
      render();
    }
  } catch { $('saveNote').textContent = '前回の診断結果を読み込めませんでした。新しく診断できます。'; }
  $('startCapture').disabled = !available;
  $('status').textContent = available ? '画面共有の呼び出しに対応しています。実際の動作は「画面共有を試す」で確認します。' : 'この起動方法では画面共有を呼び出せません。Safariで開いている場合は、結果をコピーしてお知らせください。';
  if (!available) { report = { version: 1, createdAt: Date.now(), status: 'unsupported', environment, intervals: [] }; save(); render(); }
}
