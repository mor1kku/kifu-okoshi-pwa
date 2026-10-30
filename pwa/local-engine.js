import { stored, fileId } from './storage.js';
const jobs = new Map(), files = new Map(), videos = new Map(), mediaUrls = new Map();
let worker, sequence = 0, activeJob, wakeLock, saveQueue = Promise.resolve(), kifUrl, resumeIntent;
const sessions = new Map();
let discovery = false, batchSession = null, batchAdvancing = false, batchEpoch = 0, sessionSaves = Promise.resolve();
const sessionEvent = session => document.dispatchEvent(new CustomEvent('kifu-session', { detail: structuredClone(session) }));
const pending = new Map();
const announce = (message, error = false) => document.dispatchEvent(new CustomEvent('kifu-message', { detail: { message, error } }));
function getWorker() {
  if (worker) return worker;
  worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  worker.onmessage = ({ data }) => {
    if (data.id != null) {
      const request = pending.get(data.id); if (!request) return;
      pending.delete(data.id); data.error ? request.reject(new Error(data.error)) : request.resolve(data.value); return;
    }
    if (data.type === 'message') { announce(data.message); if (activeJob) activeJob.message = data.message; }
    if (data.type === 'scanProgress') document.dispatchEvent(new CustomEvent('kifu-scan-progress', { detail: data }));
    if (!activeJob) return;
    if (data.type === 'progress') Object.assign(activeJob, data);
    if (data.type === 'checkpoint') {
      const job = activeJob;
      applySnapshot(job, data);
      persist(job).catch(error => {
        resumeIntent = null; job.saveError = error.message; job.cancelRequested = true;
        announce(error.message, true); getWorker().postMessage({ method: 'stop' });
      });
    }
  };
  worker.onerror = () => {
    const error = new Error('解析エンジンが停止しました。画面を開き直し、同じ動画を選んで途中保存から再開してください。');
    for (const request of pending.values()) request.reject(error);
    pending.clear(); worker.terminate(); worker = null;
  };
  return worker;
}
function rpc(method, payload = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); getWorker().postMessage({ id, method, payload }); });
}
function applySnapshot(job, data) {
  job.checkpoint = data.checkpoint;
  job.nextSample = data.nextSample ?? job.nextSample;
  job.result = { ...data.result, id: job.id, videoId: job.videoId, videoName: job.video.name, settings: job.settings };
  job.moveCount = data.result.moves.length;
  job.time = data.result.lastTime ?? job.time;
}
function describeResult(job) {
  if (!job.result) return;
  job.result.stopReason = job.stopReason ?? null;
  job.result.diagnostics = { status: job.status, stopReason: job.stopReason ?? null, error: job.error ?? null,
    startTime: job.settings.startTime, endTime: job.settings.endTime ?? job.video.duration,
    lastTime: job.result.lastTime, analyzedFrames: job.result.analyzedFrames,
    video: { width: job.video.width, height: job.video.height, fps: job.video.fps, duration: job.video.duration },
    browser: navigator.userAgent ?? '' };
}
function persist(job) {
  const copy = structuredClone(job);
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    await stored(`job:${copy.id}`, copy);
    await stored(`video:${copy.videoId}`, { ...copy.video, latestJob: copy.id });
  });
  return saveQueue;
}
async function loadJob(id) {
  if (jobs.has(id)) return jobs.get(id);
  const job = await stored(`job:${id}`);
  if (!job) throw Object.assign(new Error('保存済みの結果がありません。'), { status: 404 });
  if (['queued', 'running'].includes(job.status)) {
    job.status = 'cancelled'; job.stopReason = 'reload';
    if (job.result) job.result.cancelled = true;
  }
  describeResult(job);
  jobs.set(id, job); return job;
}
async function keepAwake() {
  if (document.visibilityState !== 'visible' || !navigator.wakeLock || wakeLock) return;
  try {
    const lock = await navigator.wakeLock.request('screen');
    if (document.visibilityState !== 'visible') { await lock.release(); return; }
    wakeLock = lock;
    lock.addEventListener('release', () => { if (wakeLock === lock) wakeLock = null; });
  }
  catch { announce('画面の自動ロックを防げません。解析中は画面を開いておいてください。'); }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    if (discovery) stopDiscovery();
    if (!activeJob || activeJob.cancelRequested) return;
    resumeIntent = activeJob; activeJob.stopReason = 'visibility'; activeJob.cancelRequested = true;
    getWorker().postMessage({ method: 'stop' }); activeJob.message = '画面が閉じられたため、一時停止して途中保存しています';
  } else {
    if (activeJob && !activeJob.cancelRequested) keepAwake();
    tryAutoResume();
    advanceBatch().catch(batchError);
  }
});
function tryAutoResume() {
  const job = resumeIntent;
  if (!job || activeJob || document.visibilityState !== 'visible') return;
  if (job.status !== 'cancelled' || !job.checkpoint || job.result?.edited || job.saveError || !files.has(job.videoId)) { resumeIntent = null; return; }
  resumeIntent = null;
  runJob(job, true);
  document.dispatchEvent(new CustomEvent('kifu-job-resumed', { detail: { id: job.id, videoId: job.videoId } }));
}
async function runJob(job, resume = false) {
  activeJob = job; job.status = 'running'; job.error = null; job.saveError = null; job.stopReason = null; job.cancelRequested = false;
  try {
    await keepAwake();
    const data = await rpc('analyze', { file: files.get(job.videoId), video: job.video, settings: job.settings, cancelRequested: job.cancelRequested,
      ...(resume ? { checkpoint: job.checkpoint, nextSample: job.nextSample, elapsedSeconds: job.result?.elapsedSeconds, mediaDiagnostics: job.result?.mediaDiagnostics } : {}) });
    applySnapshot(job, data);
    if (job.saveError) throw new Error(job.saveError);
    const completed = { ...job, status: data.result.cancelled ? 'cancelled' : 'complete' };
    if (completed.status === 'complete') {
      completed.progress = 1; completed.checkpoint = null; completed.stopReason = null;
      completed.message = `解析完了 · ${completed.moveCount}手`;
      if (resumeIntent === job) resumeIntent = null;
    } else {
      completed.stopReason ??= 'user';
      completed.message = completed.stopReason === 'visibility' ? '一時停止 · 画面を閉じたため途中保存しました' : '停止 · 途中まで保存しました';
    }
    describeResult(completed);
    job.message = '解析結果を端末内に保存しています';
    await persist(completed);
    if (job.saveError) throw new Error(job.saveError);
    // 保存待ちの間に押された「停止」を、古い完了コピーで巻き戻さない。
    if (completed.status === 'cancelled' && completed.stopReason === 'visibility' && job.stopReason === 'user') {
      completed.stopReason = 'user'; completed.message = '停止 · 途中まで保存しました';
      describeResult(completed); await persist(completed);
    }
    Object.assign(job, completed);
  } catch (error) {
    if (resumeIntent === job) resumeIntent = null;
    job.status = 'failed'; job.error = error.message; describeResult(job);
    announce(job.error, true);
    try { await persist(job); } catch (saveError) { job.error += ` ${saveError.message}`; }
  } finally {
    const lock = wakeLock; wakeLock = null;
    try { await lock?.release(); } catch (error) { announce(`画面ロック防止の解除を確認できません：${error.message}`); }
    if (activeJob === job) activeJob = null;
    try { await updateGameJob(job); } catch (error) { batchError(error); }
    tryAutoResume();
    advanceBatch().catch(batchError);
  }
}

export async function getSession(videoId) {
  if (!sessions.has(videoId)) {
    const session = await stored(`session:${videoId}`) ?? { version: 1, videoId, games: [], queue: { status: 'idle', gameIds: [] } };
    if (session.queue.status === 'running') session.queue.status = 'paused';
    for (const game of session.games) if (game.status === 'running') game.status = 'cancelled';
    sessions.set(videoId, session);
  }
  return structuredClone(sessions.get(videoId));
}
async function persistSession(session) {
  const copy = structuredClone(session);
  sessionSaves = sessionSaves.catch(() => {}).then(() => stored(`session:${copy.videoId}`, copy));
  await sessionSaves;
  sessions.set(copy.videoId, copy); sessionEvent(copy);
  return structuredClone(copy);
}
function validateGame(game, video, requireSide = false) {
  const s = game.settings, b = s.board;
  if (!Number.isFinite(s.startTime) || s.startTime < 0 || s.startTime >= video.duration ||
      (s.endTime != null && (!Number.isFinite(s.endTime) || s.endTime <= s.startTime || s.endTime > video.duration + .01))) throw new Error('各局の開始・終了時刻を動画内に指定してください。');
  if (!b || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(b[key])) || b.x < 0 || b.y < 0 || b.width < .05 || b.height < .05 || b.x + b.width > 1.000001 || b.y + b.height > 1.000001) throw new Error('盤面の範囲を画像内に指定してください。');
  if (!['wars', 'kioh', 'generic'].includes(s.profile) || ![6, 12, 30, 60].includes(s.sampleFps)) throw new Error('盤と読み取り密度を確認してください。');
  if (requireSide && !['sente', 'gote'].includes(s.orientation)) throw new Error('先後が未確定の局があります。「画面の下側」を指定してください。');
}
export async function saveSession(session) {
  if (activeJob || discovery || batchSession || batchAdvancing) throw new Error('停止してから対局を修正してください。');
  const video = videos.get(session.videoId);
  if (!video) throw new Error('同じ動画を選び直してください。');
  if (session.games.length > 200 || new Set(session.games.map(game => game.id)).size !== session.games.length) throw new Error('対局一覧が正しくありません。');
  session.games.forEach(game => validateGame(game, video));
  return persistSession(session);
}
export async function detectBoard(videoId, time, settings) {
  if (!canUpdate()) throw new Error('解析が終わってから操作してください。');
  return rpc('detectBoard', { file: files.get(videoId), video: videos.get(videoId), time, settings });
}
export async function discoverGames(videoId, settings) {
  if (!canUpdate()) throw new Error('解析が終わってから操作してください。');
  discovery = { cancelRequested: false };
  try {
    await keepAwake();
    const result = await rpc('findGames', { file: files.get(videoId), video: videos.get(videoId), settings, cancelRequested: discovery.cancelRequested });
    const session = await getSession(videoId);
    // 再探索しても、手修正済みの局と棋譜を上書きしない。
    for (const found of result.games) {
      const existing = session.games.find(game => Math.abs((game.detection?.sourceStartTime ?? game.settings.startTime) - found.startTime) < 1);
      if (existing) {
        // 探索を途中で止めた時には、次局の境界がまだ分からない。
        if (!existing.detection?.manual && !existing.jobId) {
          if (found.endTime != null) existing.settings.endTime = found.endTime;
          if (!existing.settings.orientation && found.orientation) existing.settings.orientation = found.orientation;
          existing.detection = { ...found.detection, sourceStartTime: found.startTime };
          existing.status = existing.settings.orientation ? 'ready' : 'needsReview';
        }
        continue;
      }
      const { detection, boundaryTime, ...setup } = found;
      session.games.push({ id: crypto.randomUUID(), settings: { ...setup, videoId }, detection: { ...detection, sourceStartTime: found.startTime },
        selected: true, jobId: null, status: found.orientation ? 'ready' : 'needsReview' });
    }
    session.games.sort((a, b) => a.settings.startTime - b.settings.startTime);
    session.discovery = { cancelled: result.cancelled, lastTime: result.lastTime };
    return await persistSession(session);
  } finally {
    discovery = false; const lock = wakeLock; wakeLock = null;
    try { await lock?.release(); } catch (error) { announce(error.message, true); }
  }
}
export function stopDiscovery() { if (discovery) { discovery.cancelRequested = true; getWorker().postMessage({ method: 'stop' }); } }
async function updateGameJob(job) {
  const session = sessions.get(job.videoId), game = session?.games.find(item => item.id === job.settings.gameId);
  if (!game || game.jobId !== job.id) return;
  game.status = job.status; game.moveCount = job.result?.moves.length ?? 0;
  await persistSession(session);
}
function batchError(error) {
  announce(error.message, true);
  if (batchSession) { batchSession.queue.status = 'paused'; sessionEvent(batchSession); batchSession = null; }
}
async function createJob(settings) {
  const video = videos.get(settings.videoId);
  if (!files.has(settings.videoId)) throw new Error('動画を選択してください。');
  validateGame({ settings }, video, true);
  const job = { id: crypto.randomUUID(), status: 'queued', videoId: video.id, video, settings: structuredClone(settings), progress: 0, message: '端末内で解析を準備しています' };
  jobs.set(job.id, job);
  const session = sessions.get(video.id), game = session?.games.find(item => item.id === settings.gameId);
  if (game) { game.jobId = job.id; game.status = 'running'; await persistSession(session); }
  return job;
}
export async function runGames(videoId) {
  if (!canUpdate()) throw new Error('処理中です。停止後に再開してください。');
  if (!files.has(videoId)) throw new Error('同じ動画を選び直してから再開してください。');
  const session = sessions.get(videoId) ?? await getSession(videoId);
  const video = videos.get(videoId);
  const selected = session.games.filter(game => game.selected && game.status !== 'complete');
  if (!selected.length) throw new Error('未解析の局を選択してください。完了した局は個別に再解析できます。');
  selected.forEach(game => validateGame(game, video, true));
  session.queue = { status: 'running', gameIds: selected.map(game => game.id), currentGameId: null };
  batchSession = session; batchEpoch++;
  try { await persistSession(session); await advanceBatch(); }
  catch (error) { batchError(error); throw error; }
}
async function advanceBatch() {
  if (!batchSession || batchAdvancing || activeJob || resumeIntent || document.visibilityState !== 'visible') return;
  batchAdvancing = true;
  const epoch = batchEpoch;
  const stopped = () => !batchSession || epoch !== batchEpoch;
  try {
    const session = sessions.get(batchSession.videoId); batchSession = session;
    const previous = session.games.find(game => game.id === session.queue.currentGameId);
    if (previous && previous.status !== 'complete') {
      session.queue.status = 'paused'; await persistSession(session); batchSession = null; return;
    }
    const game = session.queue.gameIds.map(id => session.games.find(item => item.id === id)).find(item => item && item.status !== 'complete');
    if (!game) { session.queue.status = 'complete'; await persistSession(session); batchSession = null; return; }
    session.queue.currentGameId = game.id;
    await persistSession(session);
    if (stopped()) return;
    let job = game.jobId ? await loadJob(game.jobId) : null;
    if (stopped()) return;
    const resume = Boolean(job?.checkpoint && !job.result?.edited && JSON.stringify(job.settings) === JSON.stringify({ ...game.settings, gameId: game.id }));
    if (!resume) job = await createJob({ ...game.settings, gameId: game.id });
    if (stopped()) return;
    activeJob = job; runJob(job, resume);
    document.dispatchEvent(new CustomEvent('kifu-game-started', { detail: { gameId: game.id, jobId: job.id, videoId: job.videoId } }));
  } finally { batchAdvancing = false; }
}
export async function stopGames() {
  const session = batchSession ? sessions.get(batchSession.videoId) : null; batchSession = null; batchEpoch++; resumeIntent = null;
  if (activeJob) await request(`/api/jobs/${activeJob.id}/cancel`, { method: 'POST' });
  if (session) { session.queue.status = 'paused'; await persistSession(session); }
}
export async function openVideo(file) {
  if (!file.size) throw new Error('空の動画ファイルです。');
  const id = await fileId(file);
  if (resumeIntent?.videoId !== id) resumeIntent = null;
  const video = { ...await rpc('probe', { file }), id };
  if (!Number.isFinite(video.duration) || video.duration <= 0 || !Number.isFinite(video.fps) || video.fps <= 0) throw new Error('動画の長さを取得できません。');
  // 設定候補に指し手は含めず、認識には映像だけを渡す。
  const profiles = await fetch(new URL('./profiles.json', import.meta.url)).then(response => { if (!response.ok) throw new Error('盤面設定を読み込めません。'); return response.json(); });
  video.suggestedSettings = profiles.find(profile => profile.sampleFile === file.name);
  const saved = await stored(`video:${id}`);
  await stored(`video:${id}`, { ...video, latestJob: saved?.latestJob });
  videos.set(id, video); files.set(id, file);
  for (const [oldId, url] of mediaUrls) { URL.revokeObjectURL(url); mediaUrls.delete(oldId); if (oldId !== id) files.delete(oldId); }
  mediaUrls.set(id, URL.createObjectURL(file));
  return video;
}

let previewVideo, previewQueue = Promise.resolve();
export function frameBlob(id, time) {
  const task = previewQueue.catch(() => {}).then(async () => {
    const url = mediaUrls.get(id);
    if (!url) throw new Error('映像を見るには同じ動画をもう一度選んでください。');
    previewVideo ??= Object.assign(document.createElement('video'), { muted: true, playsInline: true, preload: 'auto' });
    const waitEvent = (name, action) => new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); previewVideo.removeEventListener(name, ready); previewVideo.removeEventListener('error', fail); };
      const ready = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); reject(new Error('動画のプレビューを読み取れません。')); };
      const timer = setTimeout(fail, 20000);
      previewVideo.addEventListener(name, ready, { once: true }); previewVideo.addEventListener('error', fail, { once: true }); action();
    });
    if (previewVideo.src !== url) await waitEvent('loadeddata', () => { previewVideo.src = url; previewVideo.load(); });
    if (Math.abs(previewVideo.currentTime - time) > .00001) await waitEvent('seeked', () => { previewVideo.currentTime = time; });
    const canvas = document.createElement('canvas');
    canvas.width = Math.min(700, previewVideo.videoWidth); canvas.height = Math.round(canvas.width * previewVideo.videoHeight / previewVideo.videoWidth);
    canvas.getContext('2d').drawImage(previewVideo, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('プレビュー画像を作れません。')), 'image/jpeg', .9));
  });
  previewQueue = task; return task;
}

export async function request(path, options = {}) {
  const parts = path.split('/').filter(Boolean), id = parts[2], action = parts[3];
  if (path === '/api/sample') return { available: false, videos: [] };
  if (parts[1] === 'videos') {
    const saved = await stored(`video:${id}`);
    if (action === 'latest-result') {
      if (!saved?.latestJob) return { id: null };
      const job = await loadJob(saved.latestJob);
      return { id: job.id, moveCount: job.result?.moves.length ?? 0, cancelled: job.status !== 'complete' };
    }
    if (!saved) throw Object.assign(new Error('動画を選択してください。'), { status: 404 });
    return videos.get(id) ?? { ...saved, needsFile: true };
  }
  if (path === '/api/jobs' && options.method === 'POST') {
    if (activeJob || batchSession || discovery) throw new Error('別の動画を解析中です。');
    const settings = JSON.parse(options.body);
    resumeIntent = null;
    const job = await createJob(settings); activeJob = job;
    runJob(job); return job;
  }
  if (parts[1] !== 'jobs') throw new Error('未対応の操作です。');
  const job = await loadJob(id);
  if (action === 'cancel') {
    if (resumeIntent?.id === id) resumeIntent = null;
    job.stopReason = 'user';
    if (activeJob?.id === id) { activeJob.cancelRequested = true; getWorker().postMessage({ method: 'stop' }); }
    if (job.status === 'cancelled') { job.message = '停止 · 途中まで保存しました'; describeResult(job); await persist(job); }
    return { cancelled: true };
  }
  if (action === 'resume') {
    if (activeJob || batchSession || discovery) throw new Error('解析中です。');
    if (!job.checkpoint || job.result?.edited) throw new Error('再開できる途中保存がありません。');
    if (!files.has(job.videoId)) throw new Error('同じ動画を選び直してから再開してください。');
    resumeIntent = null;
    activeJob = job; runJob(job, true); return job;
  }
  if (action === 'record') {
    if (activeJob) throw new Error('解析が終わってから修正してください。');
    const edit = JSON.parse(options.body);
    if (edit.moves.length > 1000) throw new Error('指し手は1000手以下にしてください。');
    const result = await rpc('edit', { ...edit, result: job.result });
    const updated = { ...job, result, checkpoint: null };
    await persist(updated); jobs.set(id, updated); await updateGameJob(updated); return result;
  }
  if (action === 'compare') {
    const file = options.body.get('file');
    if (file.size > 2 * 1024 * 1024) throw new Error('棋譜は2MB以下にしてください。');
    const buffer = await file.arrayBuffer(); let text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(buffer); }
    catch { text = new TextDecoder('shift-jis', { fatal: true }).decode(buffer); }
    return rpc('compare', { text, moves: job.result.moves.map(row => row.usi) });
  }
  return job;
}
export function kifLink(result) {
  if (kifUrl) URL.revokeObjectURL(kifUrl);
  kifUrl = URL.createObjectURL(new Blob(['\ufeff', result.kif], { type: 'text/plain;charset=utf-8' })); return kifUrl;
}
export const mediaURL = id => mediaUrls.get(id) ?? '';
export const storageKey = key => `kifu-pwa:${new URL('./', import.meta.url).pathname}:${key}`;
export const canResume = id => Boolean(jobs.get(id)?.checkpoint && !jobs.get(id)?.result?.edited);
export const canUpdate = () => !activeJob && !resumeIntent && !discovery && !batchSession && !batchAdvancing && pending.size === 0;
export async function prepareOffline() {
  if (!('serviceWorker' in navigator)) throw new Error('このブラウザーではオフライン保存を使えません。SafariのHTTPSページで開いてください。');
  const registration = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('アプリの準備が終わりません。オンラインで開き直してください。')), 120000);
    navigator.serviceWorker.ready.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  return new Promise((resolve, reject) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => { channel.port1.close(); reject(new Error('ダウンロードが時間切れになりました。通信を確認して再試行してください。')); }, 300000);
    channel.port1.onmessage = ({ data }) => {
      if (data.error || data.done) { clearTimeout(timer); channel.port1.close(); data.error ? reject(new Error(data.error)) : resolve(data); }
      else announce(`オフライン用に保存しています… ${data.completed}/${data.total}`);
    };
    registration.active.postMessage({ type: 'prepare-offline' }, [channel.port2]);
  });
}
