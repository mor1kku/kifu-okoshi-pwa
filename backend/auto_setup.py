"""盤の格子検出と初期配置・連続対局の探索。"""
import cv2
import numpy as np
import shogi
from backend.recognition import FrameRecognizer, extractCells, boardCodes, templateDistance


def lineGroups(lines, axis):
    groups = []
    segments = []
    for x1, y1, x2, y2 in lines:
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if axis == 0 and dx <= max(2, dy * .006):
            segments.append(((x1 + x2) / 2, min(y1, y2), max(y1, y2)))
        elif axis == 1 and dy <= max(2, dx * .006):
            segments.append(((y1 + y2) / 2, min(x1, x2), max(x1, x2)))
    for position, start, end in sorted(segments):
        if groups and position - groups[-1]['position'] < 5:
            groups[-1]['values'].append(position)
            groups[-1]['segments'].append((int(start), int(end)))
            groups[-1]['position'] = float(np.median(groups[-1]['values']))
        else:
            groups.append({'position': float(position), 'values': [position], 'segments': [(int(start), int(end))]})
    for group in groups:
        coverage = set()
        for start, end in group['segments']:
            coverage.update(range(start, end + 1))
        group['support'] = len(coverage)
    return groups

def hypotheses(groups, length, imageWidth):
    groups = [g for g in groups if g['support'] >= imageWidth * .18]
    positions = np.array([g['position'] for g in groups])
    weights = np.array([g['support'] for g in groups])
    found = {}
    for left in range(len(groups)):
        for right in range(left + 1, len(groups)):
            for gap in (7, 8, 9):
                step = (positions[right] - positions[left]) / gap
                if not imageWidth * .018 <= step <= imageWidth / 7:
                    continue
                for offset in range(10 - gap):
                    start = positions[left] - offset * step
                    if start < -3 or start + 9 * step >= length + 3:
                        continue
                    expected = start + np.arange(10) * step
                    distances = abs(expected[:, None] - positions[None])
                    selected = distances.argmin(axis=1)
                    errors = distances[np.arange(10), selected]
                    tolerance = max(3, step * .045)
                    matched = errors < tolerance
                    if matched.sum() < 8:
                        continue
                    indices = np.flatnonzero(matched)
                    fittedStep, fittedStart = np.polyfit(indices, positions[selected[matched]], 1)
                    fittedErrors = abs(fittedStart + np.arange(10) * fittedStep - positions[selected])
                    quality = np.exp(-np.square(fittedErrors / tolerance))
                    score = float(np.mean(quality * np.minimum(weights[selected] / (step * 9), 1)))
                    key = (round(fittedStart / 2), round(fittedStep))
                    candidate = {'start': float(fittedStart), 'step': float(fittedStep), 'score': score,
                                 'selected': selected.tolist(), 'quality': quality.tolist(), 'matches': int(matched.sum())}
                    if key not in found or score > found[key]['score']:
                        found[key] = candidate
    return sorted(found.values(), key=lambda row: row['score'], reverse=True), groups


def covered(group, start, end):
    intervals = sorted((max(start, a), min(end, b)) for a, b in group['segments'] if b > start and a < end)
    length, last = 0, start
    for a, b in intervals:
        length += max(0, b - max(a, last))
        last = max(last, b)
    return length / (end - start)


def rank(vertical, horizontal, width, height):
    xs, vg = hypotheses(vertical, width, width)
    ys, hg = hypotheses(horizontal, height, width)
    results = []
    for x in xs[:40]:
        compatible = [y for y in ys if .70 <= x['step'] / y['step'] <= 1.40]
        for y in compatible[:40]:
            left, top = x['start'], y['start']
            right, bottom = left + 9 * x['step'], top + 9 * y['step']
            vc = [covered(vg[index], top, bottom) * q for index, q in zip(x['selected'], x['quality'])]
            hc = [covered(hg[index], left, right) * q for index, q in zip(y['selected'], y['quality'])]
            score = (float(np.mean(vc)) + float(np.mean(hc))) / 2
            results.append({'boxPixels': [left, top, right, bottom], 'score': score,
                            'verticalCoverage': float(np.mean(vc)), 'horizontalCoverage': float(np.mean(hc)),
                            'xMatches': x['matches'], 'yMatches': y['matches']})
    return sorted(results, key=lambda row: row['score'], reverse=True)

def fitAxis(profile, start, length, narrow=False):
    # 外周を含む10本を同時に評価し、文字の短い線より長い格子線を優先する。
    smooth = cv2.dilate(profile.astype(np.float32).reshape(1, -1), np.ones((1, 3), np.uint8)).ravel()
    extent, minScale, maxScale = (.04, .94, 1.06) if narrow else (.16, .78, 1.18)
    starts = np.arange(max(0, round(start - length * extent)), min(len(profile) - 20, round(start + length * extent)) + 1)
    lengths = np.arange(max(45, round(length * minScale)), min(len(profile) - 1, round(length * maxScale)) + 1)
    positions = np.rint(starts[:, None, None] + lengths[None, :, None] * np.arange(10)[None, None, :] / 9).astype(int)
    valid = positions[:, :, -1] < len(profile)
    samples = smooth[np.clip(positions, 0, len(profile) - 1)]
    # 下位の線も強い候補を優先し、数本だけのUI境界では決めない。
    score = np.mean(samples, axis=2) * .4 + np.quantile(samples, .25, axis=2) * .6
    score[~valid] = -1
    best = np.unravel_index(np.argmax(score), score.shape)
    return int(starts[best[0]]), int(lengths[best[1]]), float(score[best]), samples[best].tolist()


def refine(frame, box, narrow=False):
    scale = min(1, 600 / frame.shape[1])
    image = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    edgeX = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    edgeY = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    height, width = gray.shape
    x, y, w, h = box['x'] * width, box['y'] * height, box['width'] * width, box['height'] * height
    original = (x, y, w, h)
    scores = []
    for _ in range(2):
        vertical = edgeX[max(0, round(y)):min(height, round(y+h))].mean(axis=0)
        x, w, scoreX, linesX = fitAxis(vertical, original[0], original[2], narrow)
        horizontal = edgeY[:, max(0, x):min(width, x+w)].mean(axis=1)
        y, h, scoreY, linesY = fitAxis(horizontal, original[1], original[3], narrow)
        scores.append([scoreX, scoreY])
    return {'x':x/width, 'y':y/height, 'width':w/width, 'height':h/height}, scores, [linesX, linesY]

def detectBoardCandidates(frame, limit=10):
    scale = min(1, 600 / frame.shape[1])
    image = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    height, width = image.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 40, 120)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70, minLineLength=width * .13, maxLineGap=20)
    lines = [] if raw is None else raw.reshape(-1, 4)
    ranked = rank(lineGroups(lines, 0), lineGroups(lines, 1), width, height)
    candidates = []
    for row in ranked[:limit]:
        left, top, right, bottom = row['boxPixels']
        box = {'x':left/width, 'y':top/height, 'width':(right-left)/width, 'height':(bottom-top)/height}
        candidates.append({**row, 'box':box})
    return candidates


initialCodes = boardCodes(shogi.Board())


def initialCore(frame, box, profile, time=0):
    settings = {'board': box, 'profile': profile, 'orientation': 'sente', 'startTime': time, 'sampleFps': 12}
    core = FrameRecognizer(settings, frame)
    observation = core.adapter.features(extractCells(frame, box))
    if np.any(observation['occupancy'][initialCodes != 0] < .35):
        raise ValueError('初期配置の駒がそろっていません。')
    distances = []
    for code in set(initialCodes) - {0}:
        group = observation['glyph'][initialCodes == code]
        if len(group) > 1:
            for index in range(len(group)):
                distances.append(templateDistance(group[index:index + 1], np.delete(group, index, axis=0))[0])
    return core, float(np.median(distances) + np.quantile(distances, .9) * .5)


def validBox(box):
    return (all(np.isfinite(box[key]) for key in ('x', 'y', 'width', 'height')) and
            box['x'] >= 0 and box['y'] >= 0 and box['width'] >= .05 and box['height'] >= .05 and
            box['x'] + box['width'] <= 1 and box['y'] + box['height'] <= 1)


def boxOverlap(a, b):
    area = max(0, min(a['x']+a['width'], b['x']+b['width'])-max(a['x'], b['x'])) * max(0, min(a['y']+a['height'], b['y']+b['height'])-max(a['y'], b['y']))
    return area / (a['width']*a['height'] + b['width']*b['height'] - area)


def autoBoard(frame, profile, hint=None):
    candidates = detectBoardCandidates(frame)
    boxes = [row['box'] for row in candidates if row['score'] >= .35 and validBox(row['box'])]
    if hint and validBox(hint):
        boxes.insert(0, hint)
    if boxes:
        # 補正で字がぼける場合もあるので元候補も残す。
        refined = []
        for box in boxes[:2]:
            if min(box['width'] * frame.shape[1], box['height'] * frame.shape[0]) >= 60:
                refined.append(refine(frame, box)[0])
                # 全体検出に成功した枠を1マス隣へずらさず、線の位置だけ合わせる。
                refined.append(refine(frame, box, narrow=True)[0])
        boxes = boxes[:5] + refined
    accepted = []
    for box in boxes:
        if not validBox(box):
            continue
        try:
            _, quality = initialCore(frame, box, profile)
            accepted.append({'board': box, 'quality': quality})
        except ValueError:
            continue
    accepted.sort(key=lambda item: item['quality'])
    if not accepted:
        raise ValueError('初期配置の盤を見つけられませんでした。1手目が動く前の時刻と、盤と駒の見た目を確認してください。枠は手動でも指定できます。')
    best = accepted[0]
    if any(boxOverlap(best['board'], other['board']) < .5 and other['quality'] < best['quality'] + .02 for other in accepted[1:]):
        raise ValueError('複数の盤面候補があります。使う盤の範囲を手動で指定してください。')
    return best


class GameScanner:
    """棋譜の認識器とは独立して、初期配置の区間と最初に動く側を調べる。"""
    def __init__(self, settings):
        self.profile = settings['profile']
        self.box = settings.get('board')
        self.games = []
        self.pending = None
        self.lastSearch = -10
        self.lastTime = 0
        self.away = True
        self.baseCore = None
        self.missingSource = None
        self.sourceCount = 0
        self.orientation = None
        self.confirmCore = None
        self.uncertainFrames = 0
        self.lastProbe = None

    def begin(self, frame, box, time, core, quality):
        if self.games and self.games[-1]['endTime'] is None:
            self.games[-1]['endTime'] = time
        self.box = box
        self.pending = {'startTime': time, 'boundaryTime': time, 'endTime': None, 'board': dict(box),
                        'profile': self.profile, 'orientation': None, 'sampleFps': 12,
                        'detection': {'firstMoveTime': None, 'warnings': []}}
        self.baseCore = core
        self.away = False
        self.missingSource = None
        self.sourceCount = 0
        self.orientation = None
        self.confirmCore = None
        self.initialFrame = frame.copy()
        self.initialQuality = quality
        self.uncertainFrames = 0

    def feed(self, frame, time):
        self.lastTime = time
        probe = cv2.resize(frame, (72, 96), interpolation=cv2.INTER_AREA).astype(np.float32)
        sceneChanged = self.lastProbe is None or np.mean(abs(probe - self.lastProbe)) > 3
        self.lastProbe = probe
        core = None
        refined = False
        observation = self.baseCore.adapter.features(extractCells(frame, self.box)) if self.baseCore else None
        missingCount = np.count_nonzero((initialCodes != 0) & (observation['occupancy'] < .35)) if observation is not None else 40
        if self.box and (observation is None or missingCount == 0):
            try:
                core, quality = initialCore(frame, self.box, self.profile, time)
            except ValueError:
                pass
        # 盤を見失ったときだけ全体を探す。盤が動く編集動画も対象にする。
        searchInterval = .75 if self.baseCore is None or missingCount > 12 else 4
        if core is None and (self.baseCore is None or self.away) and (sceneChanged or time - self.lastSearch >= searchInterval):
            self.lastSearch = time
            try:
                found = autoBoard(frame, self.profile, self.box)
                self.box = found['board']
                core, quality = initialCore(frame, self.box, self.profile, time)
                refined = True
            except ValueError:
                pass
        if core is not None:
            if self.away or self.pending is None:
                if not refined:
                    try:
                        found = autoBoard(frame, self.profile, self.box)
                        self.box = found['board']
                        core, quality = initialCore(frame, self.box, self.profile, time)
                    except ValueError:
                        # 現在の手動枠で初期配置を確認できた場合は、その枠を残す。
                        pass
                self.begin(frame, self.box, time, core, quality)
            else:
                # 初手直前には駒が点滅する場合があるため、字の整ったフレームを残す。
                if quality < self.initialQuality - .0005:
                    self.pending['startTime'] = time
                    self.baseCore = core
                    self.initialFrame = frame.copy()
                    self.initialQuality = quality
                self.missingSource = None
                self.sourceCount = 0
                self.orientation = None
                self.confirmCore = None
            return self.snapshot()
        if self.pending is None:
            return self.snapshot()
        if self.pending in self.games:
            self.away = True
            return self.snapshot()
        observation = self.baseCore.adapter.features(extractCells(frame, self.box))
        occupancy = observation['occupancy']
        missing = np.flatnonzero((initialCodes != 0) & (occupancy < .35))
        extra = np.flatnonzero((initialCodes == 0) & (occupancy > .6))
        # 静止した初期配置は回転対称。単独で消えた移動元と後続の合法手を両方確認する。
        if len(missing) == 1 and len(extra) <= 1:
            source = int(missing[0])
            if source == self.missingSource:
                self.sourceCount += 1
            else:
                self.missingSource = source
                self.sourceCount = 1
                self.orientation = None
                self.confirmCore = None
            if self.sourceCount >= 2 and self.orientation is None:
                self.orientation = 'sente' if initialCodes[self.missingSource] <= 14 else 'gote'
                settings = {**self.baseCore.settings, 'orientation': self.orientation}
                self.confirmCore = FrameRecognizer(settings, self.initialFrame)
        if self.confirmCore is not None:
            try:
                self.confirmCore.feed(frame, time)
            except ValueError:
                self.confirmCore = None
            if self.confirmCore and self.confirmCore.moveCount():
                first = self.confirmCore.tracker.moves[0]
                source = shogi.Move.from_usi(first['usi']).from_square
                expected = self.missingSource if self.orientation == 'sente' else 80 - self.missingSource
                if source == expected:
                    self.pending['orientation'] = self.orientation
                    self.pending['detection']['firstMoveTime'] = first['time']
                    self.accept(time)
        # 初手順が失われていても候補を残し、先後を決め打ちしない。
        self.uncertainFrames = self.uncertainFrames + 1 if 2 <= len(missing) <= 8 and 1 <= len(extra) <= 8 else 0
        if self.pending not in self.games and self.uncertainFrames >= 2 and time - self.pending['startTime'] > 2:
            self.pending['detection']['warnings'] = ['初手の順序を確定できません。画面の下側を指定してください。']
            self.accept(time)
        return self.snapshot()

    def accept(self, time):
        if self.games:
            self.games[-1]['endTime'] = self.pending['boundaryTime']
        self.pending['detection']['observedMoveTime'] = time
        self.games.append(self.pending)
        self.away = True

    def snapshot(self):
        return {'games': self.games, 'lastTime': self.lastTime, 'board': self.box,
                'watchingFirstMove': self.pending is not None and self.pending not in self.games}
