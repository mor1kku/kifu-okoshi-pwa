"""ブラウザーのWorkerから共通の認識器・棋譜処理を呼び出す。"""
import json
import math
import cv2
import numpy as np
from backend.recognition import FrameRecognizer
from backend.records import compareMoves, exportRecord, parseRecord, replayMoves

recognizer = None


def validateSettings(settings):
    if settings.get('profile') not in ('kioh', 'wars', 'generic') or settings.get('orientation') not in ('sente', 'gote'):
        raise ValueError('盤面の見た目・向きを確認してください。')
    start = settings.get('startTime', 0)
    end = settings.get('endTime')
    if not isinstance(start, (float, int)) or not math.isfinite(start) or start < 0:
        raise ValueError('開始時刻が正しくありません。')
    if end is not None and (not isinstance(end, (float, int)) or not math.isfinite(end) or end <= start):
        raise ValueError('終了時刻は開始時刻より後にしてください。')
    if settings.get('sampleFps') not in (6, 12, 30, 60):
        raise ValueError('読み取り密度が正しくありません。')
    box = settings.get('board', {})
    if not all(isinstance(box.get(key), (float, int)) and math.isfinite(box[key]) for key in ('x', 'y', 'width', 'height')):
        raise ValueError('盤面範囲が正しくありません。')
    if not (box['x'] >= 0 and box['y'] >= 0 and box['width'] >= .05 and box['height'] >= .05 and
            box['x'] + box['width'] <= 1.000001 and box['y'] + box['height'] <= 1.000001):
        raise ValueError('盤面範囲を画像内に収めてください。')


def bridgeCall(method, payload, pixels=None):
    global recognizer
    value = json.loads(payload)
    if method in ('start', 'frame'):
        width, height = value['width'], value['height']
        if not 0 < width * height <= 20_000_000:
            raise ValueError('フレームの大きさが対応範囲外です。')
        data = np.asarray(pixels.to_py() if hasattr(pixels, 'to_py') else pixels, dtype=np.uint8)
        frame = cv2.cvtColor(data.reshape(height, width, 4), cv2.COLOR_RGBA2BGR)
        if method == 'start':
            validateSettings(value['settings'])
            recognizer = FrameRecognizer(value['settings'], frame)
        else:
            recognizer.feed(frame, value['time'])
        result = {'moveCount': recognizer.moveCount(), 'lastTime': recognizer.lastTime}
    elif method == 'restore':
        validateSettings(value['settings'])
        recognizer = FrameRecognizer.restore(value)
        result = {'moveCount': recognizer.moveCount(), 'lastTime': recognizer.lastTime}
    elif method == 'snapshot':
        result = recognizer.result(value.get('elapsedSeconds', 0), value.get('cancelled', False))
        result['kif'] = exportRecord([row['usi'] for row in result['moves']])
        result = {'result': result, 'checkpoint': recognizer.checkpoint()}
    elif method == 'edit':
        result = value['result']
        rows = replayMoves(value['moves'])
        kif = exportRecord(value['moves'], termination=value.get('termination'))
        changed = False
        for index, row in enumerate(rows):
            old = result['moves'][index] if index < len(result['moves']) else {}
            changed = changed or old.get('usi') != row['usi']
            row.update({'time': None if changed else old.get('time'), 'score': None if changed else old.get('score'),
                        'review': changed or old.get('review', False), 'edited': changed or old.get('edited', False)})
        result.update({'moves': rows, 'termination': value.get('termination'), 'edited': True, 'kif': kif})
        if changed:
            warning = '修正した手以降の映像時刻は未設定です。動画を手動で照合してください。'
            if warning not in result.get('warnings', []):
                result.setdefault('warnings', []).append(warning)
    elif method == 'compare':
        reference = parseRecord(value['text'])
        result = compareMoves(value['moves'], reference['moves'])
        result['referenceRows'] = reference['rows']
    else:
        raise ValueError('不明な処理です。')
    return json.dumps(result, ensure_ascii=False, allow_nan=False)
