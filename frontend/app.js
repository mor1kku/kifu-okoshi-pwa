const $ = (id) => document.getElementById(id);
const localEngine = globalThis.kifuLocal ?? null;
const kiohBox = () => ({ x: 9 / 414, y: 230 / 900, width: 396 / 414, height: 438 / 900 });
const warsBox = () => ({ x: 8 / 414, y: 254 / 900, width: 398 / 414, height: 434 / 900 });
const state = { video: null, image: null, box: kiohBox(), job: null, result: null, busy: false, frameRequest: 0, frameTime: 0, player: false, videoVersion: 0, comparisonRequest: 0 };
const formatTime = (value) => { if (value == null) return '—'; const tenths = Math.round(value * 10); return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, '0')}`; };
let frameTimer, pollTimer, dragStart;

function notice(message, error = false) {
  $('notice').hidden = !message;
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}

async function api(path, options = {}) {
  if (localEngine) return localEngine.request(path, options);
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `通信に失敗しました（${response.status}）。`;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* HTTPエラー本文がJSONでない場合はステータスを表示する。 */ }
    const error = new Error(detail); error.status = response.status; throw error;
  }
  return response.json();
}

function jsonOptions(method, body) { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
function handle(action) { return async (...args) => { try { await action(...args); } catch (error) { notice(error.message, true); } }; }
function downloadObject(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function saveLocal(key, value) { try { localStorage.setItem(localEngine ? localEngine.storageKey(key) : key, value); } catch { notice('ブラウザー内への保存が無効です。設定はJSONで保存できます。'); } }
function readLocal(key) { try { return localStorage.getItem(localEngine ? localEngine.storageKey(key) : key); } catch { return null; } }

function setBusy(value) {
  state.busy = value;
  for (const id of ['analyzeButton', 'videoFile', 'sampleButton', 'sampleSelect', 'savedResult', 'saveEdits', 'editMoves', 'termination', 'referenceFile', 'profile', 'orientation', 'startTime', 'endTime', 'sampleFps', 'useFrame', 'loadProfile', 'boxX', 'boxY', 'boxWidth', 'boxHeight']) {
    $(id).disabled = value || ((!state.video || state.video.needsFile) && ['analyzeButton', 'useFrame'].includes(id));
  }
  if (localEngine) $('resumeButton').disabled = value || !state.video || state.video.needsFile;
}

function syncBoxInputs() {
  for (const [key, id] of [['x', 'boxX'], ['y', 'boxY'], ['width', 'boxWidth'], ['height', 'boxHeight']]) $(id).value = state.box[key].toFixed(4);
}

function validateBox(box) {
  return ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(box[key])) && box.x >= 0 && box.y >= 0 && box.width >= .05 && box.height >= .05 && box.x + box.width <= 1 && box.y + box.height <= 1;
}

function restoreSettings(settings) {
  state.box = { ...settings.board }; $('profile').value = settings.profile;
  $('orientation').value = settings.orientation; $('sampleFps').value = settings.sampleFps;
  $('startTime').value = settings.startTime ?? 0; $('endTime').value = settings.endTime ?? '';
  syncBoxInputs(); drawFrame();
}

function drawFrame() {
  if (!state.image) return;
  const canvas = $('previewCanvas'), context = canvas.getContext('2d');
  canvas.width = state.image.naturalWidth; canvas.height = state.image.naturalHeight;
  context.drawImage(state.image, 0, 0);
  const { x, y, width, height } = state.box;
  const left = x * canvas.width, top = y * canvas.height, wide = width * canvas.width, high = height * canvas.height;
  context.fillStyle = 'rgba(12,25,18,.48)';
  context.fillRect(0, 0, canvas.width, top); context.fillRect(0, top + high, canvas.width, canvas.height - top - high);
  context.fillRect(0, top, left, high); context.fillRect(left + wide, top, canvas.width - left - wide, high);
  context.strokeStyle = '#83e3aa'; context.lineWidth = Math.max(2, canvas.width / 200); context.strokeRect(left, top, wide, high);
  context.strokeStyle = '#83e3aa88'; context.lineWidth = .8;
  for (let n = 1; n < 9; n++) { context.beginPath(); context.moveTo(left + wide * n / 9, top); context.lineTo(left + wide * n / 9, top + high); context.stroke(); context.beginPath(); context.moveTo(left, top + high * n / 9); context.lineTo(left + wide, top + high * n / 9); context.stroke(); }
}

async function showFrame(time) {
  if (!state.video || state.video.needsFile) return;
  const safeTime = Math.max(0, Math.min(state.video.duration - .05, Number(time) || 0));
  const request = ++state.frameRequest;
  const version = state.videoVersion;
  $('timeline').value = safeTime; $('previewTime').value = safeTime.toFixed(2);
  if (state.player) $('player').currentTime = safeTime;
  const response = localEngine ? { ok: true, blob: () => localEngine.frameBlob(state.video.id, safeTime) } : await fetch(`/api/videos/${state.video.id}/frame?time=${safeTime.toFixed(3)}`);
  if (request !== state.frameRequest || version !== state.videoVersion) return;
  if (!response.ok) throw new Error('プレビューを読み取れません。表示時刻を変更してください。');
  const url = URL.createObjectURL(await response.blob());
  const image = new Image();
  try { image.src = url; await image.decode(); if (request !== state.frameRequest || version !== state.videoVersion) return; state.image = image; state.frameTime = safeTime; drawFrame(); }
  finally { URL.revokeObjectURL(url); }
  $('frameLabel').textContent = `${formatTime(safeTime)} / ${formatTime(state.video.duration)}`;
}

async function setVideo(video, useSample = false) {
  const previousSettings = state.video?.id === video.id ? analysisSettings() : null;
  const version = ++state.videoVersion;
  ++state.frameRequest; clearTimeout(frameTimer); setBusy(true);
  state.video = video; state.result = null; state.job = null; state.savedJob = null; state.image = null;
  if (Array.from($('sampleSelect').options ?? []).some(option => option.value === video.id)) $('sampleSelect').value = video.id;
  $('previewCanvas').getContext('2d').clearRect(0, 0, $('previewCanvas').width, $('previewCanvas').height);
  clearTimeout(pollTimer); $('resultBody').hidden = true; $('emptyResult').hidden = false; $('moveCount').textContent = '0 手';
  $('videoInfo').replaceChildren();
  const name = document.createElement('strong'); name.textContent = video.name;
  $('videoInfo').append(name, `${formatTime(video.duration)} · ${video.width} × ${video.height} · ${video.fps} fps`, document.createElement('br'), `${(video.size / 1024 / 1024).toFixed(1)} MB`);
  $('videoInfo').hidden = false; $('emptyPreview').hidden = true; $('previewCanvas').hidden = false; $('previewBadge').hidden = false; $('player').hidden = true;
  $('player').src = localEngine ? localEngine.mediaURL(video.id) : `/api/videos/${video.id}/media`; state.player = false; $('togglePlayer').textContent = '動画を再生 ▷';
  for (const id of ['timeline', 'previewTime', 'prevFrame', 'nextFrame', 'togglePlayer']) $(id).disabled = Boolean(video.needsFile);
  if (localEngine) { $('resumeButton').hidden = true; $('previewCanvas').hidden = Boolean(video.needsFile); }
  $('timeline').max = Math.max(0, video.duration - .05); $('previewTime').max = video.duration - .05;
  $('progressArea').hidden = true;
  restoreSettings(previousSettings ?? video.suggestedSettings ?? { startTime: 0, endTime: null, profile: 'kioh', orientation: 'sente', sampleFps: 12, board: kiohBox() });
  $('savedResult').hidden = true;
  try {
  const saved = await api(`/api/videos/${video.id}/latest-result`);
  if (version !== state.videoVersion) return;
  state.savedJob = saved.id;
  if (saved.id) { $('savedResult').hidden = false; $('savedResult').textContent = `保存済みの解析を開く（${saved.moveCount}手${saved.cancelled ? '・途中' : ''}）`; }
  await showFrame(Number($('startTime').value));
  if (version !== state.videoVersion) return;
  notice(useSample ? 'サンプル動画を読み込みました。初期盤面と緑の枠を確認して解析してください。' : '初期配置が見える時刻へ移動し、盤面の範囲を合わせてください。');
  } finally { if (version === state.videoVersion) setBusy(false); }
}

$('videoFile').addEventListener('change', handle(async (event) => {
  const file = event.target.files[0]; if (!file) return;
  if (localEngine) {
    setBusy(true); notice('端末内の動画を確認しています…');
    try { await setVideo(await localEngine.openVideo(file)); }
    finally { setBusy(false); event.target.value = ''; }
    return;
  }
  setBusy(true); notice('動画を読み込んでいます…'); $('uploadProgress').hidden = false;
  const form = new FormData(); form.append('file', file);
  try {
    const video = await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); request.open('POST', '/api/videos');
      request.upload.onprogress = (event) => { if (event.lengthComputable) $('uploadProgress').value = event.loaded / event.total * 100; };
      request.onerror = () => reject(new Error('動画のアップロードに失敗しました。接続を確認してください。'));
      request.onload = () => { try { const result = JSON.parse(request.responseText); request.status < 300 ? resolve(result) : reject(new Error(result.detail ?? '動画を読み込めません。')); } catch { reject(new Error(`動画を読み込めません（${request.status}）。`)); } };
      request.send(form);
    });
    await setVideo(video);
  } finally { setBusy(false); $('uploadProgress').hidden = true; event.target.value = ''; }
}));

$('sampleButton').addEventListener('click', handle(async () => { setBusy(true); notice('フォルダーの動画を確認しています…'); try { await setVideo(await api('/api/sample', jsonOptions('POST', { id: $('sampleSelect').value })), true); } finally { setBusy(false); } }));
$('savedResult').addEventListener('click', handle(async () => {
  const version = state.videoVersion; setBusy(true);
  try {
  const job = await api(`/api/jobs/${state.savedJob}`);
  if (version !== state.videoVersion) return;
  if (!job.result) throw new Error('保存済みの結果がありません。最初から解析してください。');
  state.job = job.id; state.result = job.result; saveLocal('kifu-last-job', job.id);
  restoreSettings(job.result.settings); renderResult(); notice('保存済みの解析を開きました。');
  } finally { if (version === state.videoVersion) setBusy(false); }
}));
$('profile').addEventListener('change', () => {
  state.box = $('profile').value === 'kioh' ? kiohBox() : $('profile').value === 'wars' ? warsBox() : { x: .1, y: .2, width: .8, height: .6 };
  syncBoxInputs(); drawFrame();
});
for (const id of ['boxX', 'boxY', 'boxWidth', 'boxHeight']) $(id).addEventListener('change', () => {
  const box = { x: Number($('boxX').value), y: Number($('boxY').value), width: Number($('boxWidth').value), height: Number($('boxHeight').value) };
  if (!validateBox(box)) { notice('盤面範囲は画像の中に収まるよう指定してください。', true); syncBoxInputs(); return; }
  state.box = box; drawFrame();
});
function canvasPoint(event) { const rect = $('previewCanvas').getBoundingClientRect(); return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) }; }
$('previewCanvas').addEventListener('pointerdown', (event) => { if (state.busy) return; dragStart = canvasPoint(event); $('previewCanvas').setPointerCapture(event.pointerId); });
$('previewCanvas').addEventListener('pointermove', (event) => { if (!dragStart) return; const point = canvasPoint(event); const box = { x: Math.min(point.x, dragStart.x), y: Math.min(point.y, dragStart.y), width: Math.abs(point.x - dragStart.x), height: Math.abs(point.y - dragStart.y) }; if (validateBox(box)) { state.box = box; syncBoxInputs(); drawFrame(); } });
for (const name of ['pointerup', 'pointercancel']) $('previewCanvas').addEventListener(name, () => { dragStart = null; });
$('timeline').addEventListener('input', () => { clearTimeout(frameTimer); frameTimer = setTimeout(handle(() => showFrame($('timeline').value)), 180); });
$('previewTime').addEventListener('change', handle(() => showFrame($('previewTime').value)));
const displayedTime = () => state.player ? $('player').currentTime : state.frameTime;
$('prevFrame').addEventListener('click', handle(() => showFrame(displayedTime() - .1)));
$('nextFrame').addEventListener('click', handle(() => showFrame(displayedTime() + .1)));
$('useFrame').addEventListener('click', () => { const time = displayedTime(); $('startTime').value = time.toFixed(2); notice(`解析開始を ${formatTime(time)} に設定しました。`); });
$('startTime').addEventListener('change', handle(() => showFrame($('startTime').value)));
$('togglePlayer').addEventListener('click', () => {
  state.player = !state.player; $('player').hidden = !state.player; $('previewCanvas').hidden = state.player; $('previewBadge').hidden = state.player;
  $('togglePlayer').textContent = state.player ? '範囲指定に戻る' : '動画を再生 ▷';
  if (state.player) $('player').currentTime = state.frameTime;
  else { $('player').pause(); handle(showFrame)($('player').currentTime); }
});

$('saveProfile').addEventListener('click', () => { const profile = { version: 1, board: state.box, profile: $('profile').value, orientation: $('orientation').value, sampleFps: Number($('sampleFps').value) }; saveLocal('kifu-profile', JSON.stringify(profile)); downloadObject(profile, 'board-profile.json'); notice('盤面と読み取り設定をJSONに保存しました。'); });
$('loadProfile').addEventListener('click', () => $('profileFile').click());
$('profileFile').addEventListener('change', handle(async (event) => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 100000) throw new Error('設定ファイルが大きすぎます。');
    const profile = JSON.parse(await file.text());
    if (profile.version !== 1 || !profile.board || !validateBox(profile.board) || !['kioh', 'wars', 'generic'].includes(profile.profile) || !['sente', 'gote'].includes(profile.orientation) || ![6, 12, 30, 60].includes(profile.sampleFps)) throw new Error('対応する盤面設定JSONではありません。');
    if (profile.startTime != null && (!Number.isFinite(profile.startTime) || profile.startTime < 0 || (state.video && profile.startTime >= state.video.duration))) throw new Error('設定の開始時刻が動画の範囲外です。');
    state.box = profile.board; $('profile').value = profile.profile; $('orientation').value = profile.orientation; $('sampleFps').value = profile.sampleFps;
    if (profile.startTime != null) { $('startTime').value = profile.startTime; $('endTime').value = ''; await showFrame(profile.startTime); }
    syncBoxInputs(); drawFrame(); notice(`盤面設定を読み込みました。${profile.startTime != null ? '開始時刻も適用しました。' : ''}動画に合うか枠を確認してください。`);
  } finally { event.target.value = ''; }
}));

function analysisSettings() {
  return { videoId: state.video.id, startTime: Number($('startTime').value), endTime: $('endTime').value ? Number($('endTime').value) : null, sampleFps: Number($('sampleFps').value), orientation: $('orientation').value, profile: $('profile').value, board: state.box };
}
$('analyzeButton').addEventListener('click', handle(async () => {
  if (!state.video || state.busy) return;
  setBusy(true); notice('動画から指し手を推定しています。終了までこの画面で確認できます。');
  try {
    const job = await api('/api/jobs', jsonOptions('POST', analysisSettings())); state.job = job.id; state.result = null;
    $('resultBody').hidden = true; $('emptyResult').hidden = false; $('moveCount').textContent = '0 手'; $('progressArea').hidden = false; $('cancelButton').disabled = false; $('analysisProgress').value = 0;
    saveLocal('kifu-last-job', state.job); await pollJob();
  } catch (error) { setBusy(false); throw error; }
}));

async function pollJob() {
  clearTimeout(pollTimer);
  const jobId = state.job, version = state.videoVersion;
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (jobId !== state.job || version !== state.videoVersion) return;
    $('analysisProgress').value = job.progress ?? 0; $('progressMessage').textContent = job.message ?? '解析中';
    const finished = ['complete', 'cancelled', 'failed'].includes(job.status);
    const moveCount = finished ? job.result?.moves.length ?? job.moveCount ?? 0 : job.moveCount ?? job.result?.moves.length ?? 0;
    $('progressDetail').textContent = `${Math.round((job.progress ?? 0) * 100)}% · ${moveCount}手${job.time != null ? ` · ${formatTime(job.time)}` : ''}`;
    if (!finished) $('moveCount').textContent = `解析中 ${moveCount} 手`;
    if (job.status === 'failed') { setBusy(false); $('cancelButton').disabled = true; if (localEngine && job.result) { state.result = job.result; renderResult(); } notice(job.error, true); return; }
    if (['complete', 'cancelled'].includes(job.status)) {
      setBusy(false); $('cancelButton').disabled = true;
      state.result = job.result; restoreSettings(job.result.settings); renderResult();
      notice(job.status === 'cancelled' ? (job.stopReason === 'visibility' ? '画面を閉じたため一時停止しました。画面に戻ると続きから再開します。ページを開き直した場合は同じ動画を選び「途中から再開」を押してください。' : localEngine ? '途中まで保存しました。「途中から再開」で続けられます。' : '途中までの結果を保存しました。確認後、設定を変えて再解析できます。') : job.result.recognitionIncomplete ? `${job.result.moves.length}手以降で映像に追従できていない可能性があります。結果の注意事項を確認してください。` : `解析が終わりました。${moveCount}手を抽出しました。指し手を選び、映像と照合してください。`, Boolean(job.result.recognitionIncomplete)); return;
    }
    pollTimer = setTimeout(pollJob, 1000);
  } catch (error) {
    if (jobId !== state.job || version !== state.videoVersion) return;
    if (error.status >= 400 && error.status < 500) {
      setBusy(false); $('cancelButton').disabled = true;
      notice(`進捗を取得できません：${error.message} 動画を開いて再解析してください。`, true); return;
    }
    notice(`進捗を取得できません：${error.message} 再接続を試みます。`, true); pollTimer = setTimeout(pollJob, 4000);
  }
}
$('cancelButton').addEventListener('click', handle(async () => { await api(`/api/jobs/${state.job}/cancel`, { method: 'POST' }); $('cancelButton').disabled = true; $('progressMessage').textContent = '停止を待っています…'; }));
if (localEngine) {
  document.addEventListener('kifu-message', event => notice(event.detail.message, event.detail.error));
  document.addEventListener('kifu-job-resumed', event => {
    if (state.job !== event.detail.id || state.video?.id !== event.detail.videoId) return;
    clearTimeout(pollTimer); setBusy(true); state.result = null;
    $('resultBody').hidden = true; $('emptyResult').hidden = false;
    $('progressArea').hidden = false; $('cancelButton').disabled = false;
    notice('画面に戻ったので、保存した地点から自動で再開しています。');
    handle(pollJob)();
  });
  $('resumeButton').addEventListener('click', handle(async () => {
    setBusy(true);
    try {
      const job = await api(`/api/jobs/${state.job}/resume`, { method: 'POST' });
      restoreSettings(job.settings);
      $('progressArea').hidden = false; $('cancelButton').disabled = false;
      notice('保存した局面から解析を再開しています。'); await pollJob();
    } catch (error) { setBusy(false); throw error; }
  }));
}

const pieceLabels = { P: '歩', L: '香', N: '桂', S: '銀', G: '金', B: '角', R: '飛', K: '玉', '+P': 'と', '+L': '杏', '+N': '圭', '+S': '全', '+B': '馬', '+R': '龍' };
function renderBoard(sfen) {
  const [position, , hands] = sfen.split(' '); const cells = []; let promoted = false;
  for (const char of position.replaceAll('/', '')) {
    if (/\d/.test(char)) { for (let n = 0; n < Number(char); n++) cells.push(null); }
    else if (char === '+') promoted = true;
    else { cells.push({ label: pieceLabels[(promoted ? '+' : '') + char.toUpperCase()], enemy: char === char.toLowerCase(), promoted }); promoted = false; }
  }
  $('boardView').replaceChildren(...cells.map((piece) => { const cell = document.createElement('div'); cell.className = `board-square${piece?.enemy ? ' enemy' : ''}${piece?.promoted ? ' promoted' : ''}`; const label = document.createElement('span'); label.textContent = piece?.label ?? ''; cell.append(label); return cell; }));
  const stock = [[], []]; if (hands !== '-') for (const match of hands.matchAll(/(\d*)([PLNSGBRplnsgbr])/g)) stock[match[2] === match[2].toLowerCase() ? 1 : 0].push(pieceLabels[match[2].toUpperCase()] + (match[1] || ''));
  $('handsView').textContent = `先手の持駒：${stock[0].join(' ') || 'なし'}　/　後手：${stock[1].join(' ') || 'なし'}`;
}
async function selectMove(index, seek = true) {
  const row = state.result.moves[index]; if (!row) return;
  for (const button of $('moveList').children) {
    const selected = Number(button.dataset.index) === index;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-current', selected ? 'true' : 'false');
    if (selected) $('moveList').scrollTop = Math.max(0, button.offsetTop - $('moveList').offsetTop - $('moveList').clientHeight / 2);
  }
  renderBoard(row.sfen);
  if (seek && row.time != null && state.video) { await showFrame(row.time); if (state.player) { $('player').pause(); $('player').currentTime = row.time; } }
}
function renderResult() {
  const result = state.result; if (!result) return;
  state.savedJob = state.job; $('savedResult').hidden = false;
  $('savedResult').textContent = `保存済みの解析を開く（${result.moves.length}手${result.cancelled ? '・途中' : ''}）`;
  $('resultBody').hidden = false; $('emptyResult').hidden = true; $('moveCount').textContent = `${result.moves.length} 手`;
  const reviewCount = result.moves.filter((row) => row.review).length;
  $('resultSummary').textContent = `${result.cancelled ? '途中結果' : '解析結果'}：${result.moves.length}手 · 要確認 ${reviewCount}手${result.edited ? ' · 修正済み' : ''}${result.cancelled ? result.stopReason === 'visibility' ? ' · 画面を閉じたため一時停止' : ' · 途中停止' : ''}${result.recognitionIncomplete ? ' · 読み取り不足の可能性' : ''}${result.engineVersion ? ` · 解析v${result.engineVersion}` : ''}。終局理由は映像で確認してください。`;
  $('moveList').replaceChildren(...result.moves.map((row, index) => {
    const button = document.createElement('button'); button.className = 'move-row'; button.dataset.index = index;
    for (const [className, value] of [['move-number', row.number], ['', (index % 2 ? '△ ' : '▲ ') + row.label], ['move-time', formatTime(row.time)], ['review-mark', row.review ? '!' : '']]) { const span = document.createElement('span'); span.className = className; span.textContent = value; button.append(span); }
    button.title = `${row.usi} / ${row.review ? '要確認' : '推定'}${row.score != null ? ' / 照合スコア ' + row.score.toFixed(3) : ''}`;
    button.addEventListener('click', handle(() => selectMove(index))); return button;
  }));
  $('warnings').replaceChildren(...(result.warnings ?? []).map((warning) => { const p = document.createElement('div'); p.textContent = typeof warning === 'string' ? warning : warning.message; return p; }));
  $('editMoves').value = result.moves.map((row) => row.usi).join('\n'); $('termination').value = result.termination || '';
  $('downloadKif').href = localEngine ? localEngine.kifLink(result) : `/api/jobs/${state.job}/kif`; $('comparison').textContent = '';
  if (localEngine) $('resumeButton').hidden = !localEngine.canResume(state.job);
  if (result.moves.length) handle(selectMove)(result.moves.length - 1, true); else renderBoard('lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1');
}
$('downloadJson').addEventListener('click', () => { if (state.result) downloadObject(state.result, 'analysis.json'); });
$('saveEdits').addEventListener('click', handle(async () => {
  const jobId = state.job, version = state.videoVersion;
  setBusy(true);
  try {
  const moves = $('editMoves').value.trim().split(/\s+/).filter(Boolean);
  const result = await api(`/api/jobs/${jobId}/record`, jsonOptions('PUT', { moves, termination: $('termination').value || null }));
  if (jobId !== state.job || version !== state.videoVersion) return;
  state.result = result;
  renderResult(); notice('全手の合法性を確認し、修正した棋譜を保存しました。');
  } finally { if (jobId === state.job && version === state.videoVersion) setBusy(false); }
}));
$('referenceFile').addEventListener('change', handle(async (event) => {
  const file = event.target.files[0]; if (!file) return;
  const jobId = state.job, version = state.videoVersion, record = state.result, request = ++state.comparisonRequest;
  try {
    const form = new FormData(); form.append('file', file);
    const result = await api(`/api/jobs/${jobId}/compare`, { method: 'POST', body: form });
    if (jobId !== state.job || version !== state.videoVersion || record !== state.result || request !== state.comparisonRequest) return;
    const expected = result.referenceRows[result.firstMismatch - 1], predicted = state.result.moves[result.firstMismatch - 1];
    $('comparison').textContent = result.exactMatch ? `全${result.referenceCount}手が完全一致しました。` : `正解${result.referenceCount}手中、同じ手数で${result.positionMatches}手一致。冒頭から${result.prefixMatch}手連続一致。最初の差は${result.firstMismatch}手目：推定 ${predicted?.usi ?? 'なし'} ／ 正解 ${expected?.usi ?? 'なし'}。`;
  }
  finally { event.target.value = ''; }
}));

syncBoxInputs();
handle(async () => {
  setBusy(true);
  try {
  const sample = await api('/api/sample');
  for (const id of ['sampleLabel', 'sampleSelect', 'sampleButton']) $(id).hidden = !sample.available;
  $('sampleSelect').replaceChildren(...(sample.videos ?? []).map((video) => { const option = document.createElement('option'); option.value = video.id; option.textContent = video.name; return option; }));
  const lastJob = readLocal('kifu-last-job');
  if (lastJob) {
    try {
      const job = await api(`/api/jobs/${lastJob}`);
      if (job.result) { await setVideo(await api(`/api/videos/${job.result.videoId}`)); state.job = lastJob; state.result = job.result; restoreSettings(job.result.settings); renderResult(); notice(localEngine ? '前回の結果を復元しました。映像の確認・解析の再開には同じ動画を選んでください。' : '前回の解析結果を復元しました。'); }
      else if (['queued', 'running'].includes(job.status)) { await setVideo(await api(`/api/videos/${job.videoId}`)); state.job = lastJob; restoreSettings(job.settings); setBusy(true); $('progressArea').hidden = false; pollJob(); return; }
    } catch (error) { notice(`前回の解析結果を復元できませんでした：${error.message}`); }
  }
  } finally { if (!state.job || state.result) setBusy(false); }
})();
