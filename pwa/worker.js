import { loadPyodide } from './vendor/pyodide/pyodide.mjs';
import { Input, ALL_FORMATS, BlobSource, CanvasSink } from './vendor/mediabunny.mjs';

let runtimePromise, bridge, activeRun = false, stopRequested = false;
const emit = (value) => self.postMessage(value);
async function runtime() {
  if (!runtimePromise) runtimePromise = (async () => {
    emit({ type: 'message', message: '端末内の解析エンジンを準備しています…（初回はダウンロードがあります）' });
    const python = await loadPyodide({ indexURL: new URL('./vendor/pyodide/', import.meta.url).href });
    await python.loadPackage(['numpy', 'opencv-python']);
    const response = await fetch(new URL('./python.zip', import.meta.url));
    if (!response.ok) throw new Error('解析プログラムを取得できません。オンラインで開き直してください。');
    python.unpackArchive(await response.arrayBuffer(), 'zip', { extractDir: '/app' });
    python.runPython("import sys\nsys.path.insert(0, '/app')\nfrom backend.browser_bridge import bridgeCall");
    bridge = python.globals.get('bridgeCall');
    return python;
  })().catch(error => { runtimePromise = null; throw error; });
  return runtimePromise;
}
function call(method, data, pixels) { return JSON.parse(bridge(method, JSON.stringify(data), pixels)); }

async function openInput(file) {
  if (typeof VideoDecoder === 'undefined' || typeof OffscreenCanvas === 'undefined') {
    throw new Error('このブラウザーは端末内動画解析に未対応です。iOSを更新し、Safariで開いてください。');
  }
  const input = new Input({ source: new BlobSource(file), formats: ALL_FORMATS });
  try {
    const track = await input.getPrimaryVideoTrack();
    if (!track || !await track.canDecode()) throw new Error('この動画形式を端末内で読み取れません。H.264のMP4で試してください。');
    return { input, track };
  } catch (error) { input.dispose(); throw error; }
}

async function probe(file) {
  const { input, track } = await openInput(file);
  try {
    const stats = await track.computePacketStats();
    return { name: file.name, size: file.size, duration: await track.computeDuration(),
      width: await track.getDisplayWidth(), height: await track.getDisplayHeight(), fps: stats.averagePacketRate };
  } finally { input.dispose(); }
}

function cropFrame(wrapped, box) {
  const canvas = wrapped.canvas;
  // 縮小は既存のOpenCVで行い、Canvasでは元の画素を切り出すだけにする。
  // Pythonのroundと同じ偶数丸めにして、ちょうど半画素の境界も合わせる。
  const roundEven = value => value % 1 === .5 ? 2 * Math.round(value / 2) : Math.round(value);
  const x = roundEven(box.x * canvas.width), y = roundEven(box.y * canvas.height);
  const width = roundEven((box.x + box.width) * canvas.width) - x;
  const height = roundEven((box.y + box.height) * canvas.height) - y;
  if (x < 0 || y < 0 || width < 1 || height < 1 || x + width > canvas.width || y + height > canvas.height) throw new Error('盤面の枠を画像内に指定してください。');
  const pixels = canvas.getContext('2d', { willReadFrequently: true }).getImageData(x, y, width, height).data;
  return { pixels, width, height };
}

async function analyze(data) {
  if (activeRun) throw new Error('別の動画を解析中です。');
  activeRun = true; stopRequested = Boolean(data.cancelRequested);
  let input, initialized = false, nextSample;
  const began = performance.now();
  const elapsedBefore = data.elapsedSeconds ?? 0;
  const snapshot = (cancelled) => call('snapshot', { elapsedSeconds: elapsedBefore + (performance.now() - began) / 1000, cancelled });
  try {
    await runtime();
    const opened = await openInput(data.file); input = opened.input;
    const { settings, video } = data;
    const endTime = Math.min(settings.endTime ?? video.duration, video.duration);
    if (!Number.isFinite(settings.startTime) || settings.startTime < 0 || settings.startTime >= endTime) throw new Error('開始・終了時刻を動画の範囲内で指定してください。');
    if (![6, 12, 30, 60].includes(settings.sampleFps)) throw new Error('読み取り密度が正しくありません。');
    const interval = 1 / Math.min(settings.sampleFps, video.fps);
    const sink = new CanvasSink(opened.track, { poolSize: 1, alpha: false });
    nextSample = settings.startTime + interval;
    let lastTime = settings.startTime, lastSaved = 0, lastProgress = 0;
    if (data.checkpoint) {
      call('restore', data.checkpoint); initialized = true;
      lastTime = data.checkpoint.lastTime;
      nextSample = data.nextSample ?? lastTime + interval - .5 / video.fps;
    } else {
      const first = await sink.getCanvas(settings.startTime);
      if (!first) throw new Error('初期配置のフレームを読み取れません。');
      const frame = cropFrame(first, settings.board);
      call('start', { settings: { ...settings, board: { x: 0, y: 0, width: 1, height: 1 } }, width: frame.width, height: frame.height }, frame.pixels);
      initialized = true;
    }
    emit({ type: 'checkpoint', ...snapshot(true), nextSample });
    emit({ type: 'message', message: '端末内で解析しています。画面を開いたままお待ちください。途中で停止して再開することもできます。' });
    if (stopRequested) return { ...snapshot(true), nextSample };
    for await (const wrapped of sink.canvases(lastTime, endTime)) {
      if (stopRequested) break;
      const time = wrapped.timestamp;
      if (time <= lastTime || time + .0001 < nextSample) continue;
      const frame = cropFrame(wrapped, settings.board);
      const info = call('frame', { time, width: frame.width, height: frame.height }, frame.pixels);
      lastTime = time; nextSample = time + interval - .5 / video.fps;
      const now = performance.now();
      if (now - lastProgress > 400) {
        emit({ type: 'progress', time, moveCount: info.moveCount, progress: Math.min(.999, (time - settings.startTime) / (endTime - settings.startTime)), message: `${info.moveCount}手を抽出しています` });
        lastProgress = now;
        // Workerの停止要求を処理し、フレームを無制限に積まない。
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (now - lastSaved > 5000) { emit({ type: 'checkpoint', ...snapshot(true), nextSample }); lastSaved = now; }
    }
    if (!stopRequested && lastTime < endTime - Math.max(1, interval * 2)) throw new Error(`${lastTime.toFixed(1)}秒で動画の読み取りが途切れました。途中保存から再開できます。`);
    return { ...snapshot(stopRequested), nextSample };
  } catch (error) {
    if (initialized) emit({ type: 'checkpoint', ...snapshot(true), nextSample });
    throw error;
  } finally { input?.dispose(); activeRun = false; }
}

self.onmessage = async ({ data }) => {
  if (data.method === 'stop') { stopRequested = true; return; }
  try {
    let value;
    if (data.method === 'probe') value = await probe(data.payload.file);
    else if (data.method === 'analyze') value = await analyze(data.payload);
    else if (data.method === 'warmup') { await runtime(); value = true; }
    else { if (activeRun) throw new Error('解析が終わってから操作してください。'); await runtime(); value = call(data.method, data.payload); }
    emit({ id: data.id, value });
  } catch (error) {
    const message = error.message || String(error);
    const expected = message.match(/(?:ValueError|RuntimeError): ([^\n]+)/);
    emit({ id: data.id, error: expected ? expected[1] : message });
  }
};
