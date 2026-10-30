"""ローカルとDockerで共通の動画解析API。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, RLock
from copy import deepcopy
from tempfile import NamedTemporaryFile
import json
import os
import secrets
import shutil
import uuid

import cv2
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from backend.records import compareMoves, decodeRecord, exportRecord, parseRecord, replayMoves

rootDir = Path(__file__).resolve().parents[1]
dataDir = Path(os.getenv('KIFU_DATA_DIR', str(rootDir / '.local' / 'data'))).resolve()
dataDir.mkdir(parents=True, exist_ok=True)
maxUploadBytes = int(os.getenv('KIFU_MAX_UPLOAD_MB', '2048')) * 1024 * 1024
worker = ThreadPoolExecutor(max_workers=1)
jobs = {}
jobLock = RLock()


@asynccontextmanager
async def lifespan(app):
    global worker
    if worker._shutdown:
        worker = ThreadPoolExecutor(max_workers=1)
    yield
    for job in jobs.values():
        job['cancel'].set()
    worker.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='棋譜おこし', lifespan=lifespan)


@app.middleware('http')
async def accessControl(request: Request, callNext):
    accessToken = os.getenv('KIFU_ACCESS_TOKEN', '')
    if accessToken:
        import base64
        supplied = ''
        try:
            scheme, credentials = request.headers.get('authorization', '').split(' ', 1)
            if scheme.lower() == 'basic':
                supplied = base64.b64decode(credentials).decode().split(':', 1)[1]
        except (ValueError, UnicodeDecodeError, IndexError):
            pass
        if not secrets.compare_digest(supplied.encode('utf-8'), accessToken.encode('utf-8')):
            return Response('認証が必要です。', status_code=401,
                            headers={'WWW-Authenticate': 'Basic realm="Kifu Okoshi", charset="UTF-8"'})
    # 他サイトからlocalhostの動画を操作するリクエストを拒否する。
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '異なるサイトからの操作は許可されていません。'}, status_code=403)
    response = await callNext(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


class BoardBox(BaseModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(ge=0.05, le=1)
    height: float = Field(ge=0.05, le=1)

    @model_validator(mode='after')
    def checkBounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError('盤面範囲が画像からはみ出しています。')
        return self


class AnalysisSettings(BaseModel):
    videoId: str
    startTime: float = Field(default=0, ge=0)
    endTime: float | None = Field(default=None, gt=0)
    sampleFps: float = Field(default=12, ge=1, le=60)
    orientation: str = Field(default='sente', pattern='^(sente|gote)$')
    profile: str = Field(default='kioh', pattern='^(kioh|wars|generic)$')
    board: BoardBox


class RecordUpdate(BaseModel):
    moves: list[str] = Field(max_length=2000)
    termination: str | None = None


def safeId(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(404, 'データが見つかりません。') from exc


def saveJson(path: Path, value):
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False)
        # Windowsで同じ保存先を同時に置換すると拒否されるため、置換を直列化する。
        with jobLock:
            temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def readVideo(videoId: str) -> dict:
    path = dataDir / safeId(videoId) / 'video.json'
    if not path.exists():
        raise HTTPException(404, '動画が見つかりません。もう一度選択してください。')
    return json.loads(path.read_text(encoding='utf-8'))


def probeVideo(path: Path, name: str, videoId: str) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        fps, count = capture.get(cv2.CAP_PROP_FPS), capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not ok or not 0 < fps <= 1000 or count <= 0:
            raise ValueError('この動画を読み取れません。MP4（H.264）などに変換して再度お試しください。')
        height, width = frame.shape[:2]
        return {'id': videoId, 'name': name, 'path': str(path), 'width': width, 'height': height,
                'fps': round(fps, 3), 'duration': round(count / fps, 3), 'size': path.stat().st_size}
    finally:
        capture.release()


def publicVideo(video: dict):
    public = {key: value for key, value in video.items() if key != 'path'}
    for path in (rootDir / 'profiles').glob('*.json'):
        profile = json.loads(path.read_text(encoding='utf-8'))
        if profile.get('sampleFile') == video.get('name'):
            public['suggestedSettings'] = profile
            break
    return public


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/sample')
def sampleInfo():
    videos = sampleVideos()
    return {'available': bool(videos), 'videos': [{'id': key, 'name': path.name} for key, path in videos.items()]}


def sampleVideos():
    sampleDir = os.getenv('KIFU_SAMPLE_DIR')
    if not sampleDir or not Path(sampleDir).is_dir():
        return {}
    videos = sorted(path for path in Path(sampleDir).iterdir()
                    if path.is_file() and path.suffix.lower() in ('.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv'))
    # 同じパスの動画が置き換わったときに古い棋譜を関連付けない。
    return {str(uuid.uuid5(uuid.NAMESPACE_URL, f'{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}')): path
            for path in videos}


class SampleSelection(BaseModel):
    id: str


@app.post('/api/sample')
def loadSample(selection: SampleSelection | None = None):
    videos = sampleVideos()
    if not videos:
        raise HTTPException(404, 'サンプル動画が見つかりません。')
    videoId = safeId(selection.id) if selection else next(iter(videos))
    if videoId not in videos:
        raise HTTPException(404, '選択した動画がありません。フォルダー一覧を読み直してください。')
    path = videos[videoId]
    folder = dataDir / videoId
    folder.mkdir(exist_ok=True)
    try:
        video = probeVideo(path, path.name, videoId)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    saveJson(folder / 'video.json', video)
    return publicVideo(video)


@app.post('/api/videos')
def uploadVideo(file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv'):
        raise HTTPException(422, '対応する動画ファイルを選択してください。')
    videoId = str(uuid.uuid4())
    folder = dataDir / videoId
    folder.mkdir()
    path = folder / ('video' + suffix)
    size = 0
    try:
        with path.open('wb') as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > maxUploadBytes:
                    raise HTTPException(413, f'動画は {maxUploadBytes // 1024 // 1024} MB 以下にしてください。')
                output.write(chunk)
        video = probeVideo(path, Path(file.filename).name, videoId)
        saveJson(folder / 'video.json', video)
    except Exception as exc:
        path.unlink(missing_ok=True)
        folder.rmdir()
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise
    finally:
        file.file.close()
    return publicVideo(video)


@app.get('/api/videos/{videoId}/frame')
def videoFrame(videoId: str, time: float = 0):
    video = readVideo(videoId)
    if not 0 <= time < video['duration']:
        raise HTTPException(422, '動画の範囲内の時刻を指定してください。')
    capture = cv2.VideoCapture(video['path'])
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, time * 1000)
        ok, frame = capture.read()
        if not ok:
            raise HTTPException(422, '指定した時刻のフレームを読み取れません。')
        scale = min(1, 1200 / frame.shape[0])
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
        ok, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise HTTPException(500, 'プレビュー画像を生成できません。')
        return Response(data.tobytes(), media_type='image/jpeg')
    finally:
        capture.release()


@app.get('/api/videos/{videoId}')
def videoInfo(videoId: str):
    return publicVideo(readVideo(videoId))


@app.get('/api/videos/{videoId}/latest-result')
def latestResult(videoId: str):
    videoId = safeId(videoId)
    readVideo(videoId)
    for path in sorted(dataDir.glob('*.result.json'), key=lambda path: path.stat().st_mtime, reverse=True):
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('videoId') == videoId:
            return {'id': result['id'], 'moveCount': len(result['moves']), 'cancelled': result.get('cancelled', False)}
    return {'id': None}


@app.get('/api/videos/{videoId}/media')
def videoMedia(videoId: str):
    video = readVideo(videoId)
    return FileResponse(video['path'])


def jobView(job: dict):
    with jobLock:
        return deepcopy({key: value for key, value in job.items() if key not in ('cancel',)})


def runAnalysis(jobId: str, video: dict, settings: dict):
    job = jobs[jobId]
    def progress(value):
        with jobLock:
            job.update(value)
    try:
        from backend.recognition import analyzeVideo
        progress({'status': 'running'})
        result = analyzeVideo(video['path'], settings, progress, job['cancel'].is_set)
        result.update({'id': jobId, 'videoId': video['id'], 'settings': settings, 'termination': None})
        saveJson(dataDir / f'{jobId}.result.json', result)
        progress({'status': 'cancelled' if job['cancel'].is_set() else 'complete', 'result': result,
                  'progress': job['progress'] if job['cancel'].is_set() else 1,
                  'message': '解析を停止しました。' if job['cancel'].is_set() else '解析が終わりました。映像と照合してください。'})
    except Exception as exc:
        import logging
        logging.exception('動画解析に失敗しました')
        progress({'status': 'failed', 'error': str(exc), 'message': '解析できませんでした。設定を確認してください。'})


@app.post('/api/jobs')
def startAnalysis(settings: AnalysisSettings):
    with jobLock:
        video = readVideo(settings.videoId)
        endTime = settings.endTime if settings.endTime is not None else video['duration']
        if not settings.startTime < endTime <= video['duration']:
            raise HTTPException(422, '開始・終了時刻を動画の範囲内で指定してください。')
        if any(job['status'] in ('queued', 'running') for job in jobs.values()):
            raise HTTPException(409, 'ほかの解析が進行中です。完了するか停止してから再実行してください。')
        jobId = str(uuid.uuid4())
        jobs[jobId] = {'id': jobId, 'status': 'queued', 'progress': 0, 'moveCount': 0,
                       'videoId': video['id'], 'settings': settings.model_dump(),
                       'message': '解析を準備しています。', 'cancel': Event()}
        try:
            worker.submit(runAnalysis, jobId, video, settings.model_dump())
        except RuntimeError as exc:
            jobs.pop(jobId)
            raise HTTPException(503, '解析処理を開始できません。サーバーを再起動してください。') from exc
    return jobView(jobs[jobId])


@app.get('/api/jobs/{jobId}')
def getJob(jobId: str):
    jobId = safeId(jobId)
    if jobId in jobs:
        return jobView(jobs[jobId])
    path = dataDir / f'{jobId}.result.json'
    if path.exists():
        result = json.loads(path.read_text(encoding='utf-8'))
        return {'id': jobId, 'status': 'cancelled' if result.get('cancelled') else 'complete',
                'progress': None if result.get('cancelled') else 1, 'result': result}
    raise HTTPException(404, '解析結果が見つかりません。')


@app.post('/api/jobs/{jobId}/cancel')
def cancelJob(jobId: str):
    jobId = safeId(jobId)
    if jobId not in jobs:
        raise HTTPException(404, '解析が見つかりません。')
    jobs[jobId]['cancel'].set()
    return {'status': 'cancelling'}


@app.put('/api/jobs/{jobId}/record')
def updateRecord(jobId: str, update: RecordUpdate):
    with jobLock:
        return saveRecord(safeId(jobId), update)


def saveRecord(jobId: str, update: RecordUpdate):
    job = getJob(jobId)
    if job['status'] in ('queued', 'running'):
        raise HTTPException(409, '解析終了後に修正してください。')
    result = job.get('result')
    if not result:
        raise HTTPException(422, '修正できる棋譜がありません。')
    try:
        rows = replayMoves(update.moves)
        exportRecord(update.moves, termination=update.termination)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = result['moves']
    changed = False
    for index, row in enumerate(rows):
        old = previous[index] if index < len(previous) else {}
        changed = changed or old.get('usi') != row['usi']
        row.update({'time': None if changed else old.get('time'), 'score': None if changed else old.get('score'),
                    'review': changed or old.get('review', False), 'edited': changed or old.get('edited', False)})
    result.update({'moves': rows, 'termination': update.termination, 'edited': True})
    if changed:
        warning = '修正した手以降の映像時刻は未設定です。動画を手動で照合してください。'
        if warning not in result.get('warnings', []):
            result.setdefault('warnings', []).append(warning)
    saveJson(dataDir / f'{jobId}.result.json', result)
    if jobId in jobs:
        with jobLock:
            jobs[jobId]['result'] = result
    return result


@app.get('/api/jobs/{jobId}/kif')
def downloadKif(jobId: str):
    result = getJob(jobId).get('result')
    if not result:
        raise HTTPException(422, '出力できる棋譜がありません。')
    content = exportRecord([row['usi'] for row in result['moves']], termination=result.get('termination'))
    return Response(content.encode('utf-8-sig'), media_type='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="record.kif"'})


@app.post('/api/jobs/{jobId}/compare')
async def compareRecord(jobId: str, file: UploadFile = File(...)):
    result = getJob(jobId).get('result')
    if not result:
        raise HTTPException(422, '先に動画の解析を行ってください。')
    try:
        content = await file.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError('棋譜ファイルは2MB以下にしてください。')
        reference = parseRecord(decodeRecord(content))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
    comparison = compareMoves([row['usi'] for row in result['moves']], reference['moves'])
    comparison['referenceRows'] = reference['rows']
    return comparison


@app.delete('/api/videos/{videoId}')
def deleteVideo(videoId: str):
    with jobLock:
        return removeVideo(safeId(videoId))


def removeVideo(videoId: str):
    readVideo(videoId)
    if any(job['status'] in ('queued', 'running') for job in jobs.values()):
        raise HTTPException(409, '解析中は削除できません。')
    folder = dataDir / safeId(videoId)
    # サンプル元ファイルはこのフォルダの外にあるため削除対象にならない。
    shutil.rmtree(folder)
    for path in dataDir.glob('*.result.json'):
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('videoId') == videoId:
            jobs.pop(result['id'], None)
            path.unlink()
    return {'deleted': True}


app.mount('/', StaticFiles(directory=rootDir / 'frontend', html=True), name='frontend')
