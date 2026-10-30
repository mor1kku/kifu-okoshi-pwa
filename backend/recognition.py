"""初期局面の駒画像と合法手を照合する、外部API不要の認識器。"""
from time import monotonic
import cv2
import numpy as np
import shogi
import shogi.KIF


def boardCodes(board):
    return np.array([0 if board.piece_at(square) is None else
                     board.piece_at(square).color * 14 + board.piece_type_at(square)
                     for square in range(81)], dtype=np.int32)


def extractCells(frame, box, orientation='sente'):
    height, width = frame.shape[:2]
    left, top = round(box['x'] * width), round(box['y'] * height)
    right, bottom = round((box['x'] + box['width']) * width), round((box['y'] + box['height']) * height)
    if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
        raise ValueError('盤面の範囲が動画からはみ出しています。')
    crop = cv2.resize(frame[top:bottom, left:right], (396, 438), interpolation=cv2.INTER_AREA)
    if orientation == 'gote':
        crop = cv2.rotate(crop, cv2.ROTATE_180)
    cells = []
    for square in range(81):
        row, column = divmod(square, 9)
        centerX, centerY = (column + .5) * 44, (row + .5) * (438 / 9)
        cells.append(crop[round(centerY - 21):round(centerY + 21), round(centerX - 19):round(centerX + 19)])
    return np.stack(cells)


class KiohAdapter:
    """黄色の駒と青い盤の棋桜表示を分離する。"""
    def features(self, cells):
        hsv = np.stack([cv2.cvtColor(cell, cv2.COLOR_BGR2HSV) for cell in cells])
        yellow = ((hsv[:, :, :, 0] >= 8) & (hsv[:, :, :, 0] <= 42) &
                  (hsv[:, :, :, 1] > 90) & (hsv[:, :, :, 2] > 55)).astype(np.float32)
        occupancy = yellow.mean(axis=(1, 2))
        direction = yellow[:, :10].mean(axis=(1, 2)) - yellow[:, -10:].mean(axis=(1, 2))
        ink = (hsv[:, 6:36, 6:32, 2] < 100).astype(np.float32)
        glyph = np.stack([cv2.resize(cell, (16, 24), interpolation=cv2.INTER_AREA) for cell in ink])
        return {'glyph': glyph, 'occupancy': np.clip((occupancy - .10) / .30, 0, 1), 'direction': direction}

    def initialize(self, observation, board):
        codes = boardCodes(board)
        occupied = codes != 0
        occupancy = observation['occupancy']
        if (occupancy[occupied] > .6).sum() < 38 or (occupancy[~occupied] > .6).sum() > 3:
            raise ValueError('平手の初期配置を確認できません。演出が消えた後、1手目が動く前の時刻と盤面範囲を指定してください。駒の色が異なる場合は「ほかのアプリ」を選んでください。')
        self.directions = {color: np.median(observation['direction'][(codes > 0) & ((codes - 1) // 14 == color)]) for color in (0, 1)}
        if self.directions[0] >= self.directions[1]:
            raise ValueError('駒の向きが初期配置と一致しません。「画面の下側」の先手・後手を確認してください。')

    def costs(self, observation, templates):
        occupancy = observation['occupancy']
        costs = np.zeros((81, 29), dtype=np.float32)
        costs[:, 0] = occupancy
        for code in range(1, 29):
            color = (code - 1) // 14
            textCost = templateDistance(observation['glyph'], templates[code]) if code in templates else np.full(81, .14, dtype=np.float32)
            shapeCost = np.maximum(0, abs(observation['direction'] - self.directions[color]) - .07)
            costs[:, code] = 1 - occupancy + textCost * 2 + shapeCost * 1.7
        return costs


class GenericAdapter:
    """同一動画内の初期配置から白黒の駒字を照合する試験用アダプター。"""
    def features(self, cells):
        gray = np.stack([cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) for cell in cells])
        # 地色から暗い文字と赤い成駒を取り出す。
        region = gray[:, 6:36, 6:32].astype(np.float32)
        background = np.percentile(region, 80, axis=(1, 2))[:, None, None]
        color = cells[:, 6:36, 6:32].astype(np.float32)
        red = (color[:, :, :, 2] > color[:, :, :, 1] + 45) & (color[:, :, :, 2] > color[:, :, :, 0] + 45)
        ink = ((region < background - 40) | red).astype(np.float32)
        glyph = np.stack([cv2.resize(cell, (16, 24), interpolation=cv2.INTER_AREA) for cell in ink])
        return self.observation(glyph)

    def observation(self, glyph):
        occupancy = (np.clip(templateDistance(glyph, self.emptyTemplates) * 10, 0, 1)
                     if hasattr(self, 'emptyTemplates') else np.ones(81))
        return {'glyph': glyph, 'occupancy': occupancy, 'direction': np.zeros(81)}

    def initialize(self, observation, board):
        codes = boardCodes(board)
        self.emptyTemplates = observation['glyph'][codes == 0].copy()
        occupiedInk = observation['glyph'][codes != 0].mean()
        emptyInk = self.emptyTemplates.mean()
        if occupiedInk < .02 or occupiedInk < emptyInk + .025:
            raise ValueError('駒字と空のマスを区別できません。盤面範囲と初期配置の時刻を調整してください。この見た目は未対応の可能性があります。')

    def costs(self, observation, templates):
        costs = np.zeros((81, 29), dtype=np.float32)
        costs[:, 0] = templateDistance(observation['glyph'], self.emptyTemplates) * 6
        for code in range(1, 29):
            costs[:, code] = templateDistance(observation['glyph'], templates[code]) * 6 if code in templates else .7
        return costs


class WarsAdapter(GenericAdapter):
    """木目や橙色のハイライトを除き、二文字の駒字を照合する。"""
    normalConfirmations = 2
    dropEvidenceWeight = .4

    def features(self, cells):
        color = cells[:, 4:38, 4:34].astype(np.float32)
        gray = np.stack([cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) for cell in cells])[:, 4:38, 4:34]
        background = np.percentile(gray, 80, axis=(1, 2))[:, None, None]
        dark = gray < np.minimum(90, background - 45)
        red = (color[:, :, :, 2] > color[:, :, :, 1] * 1.8) & (color[:, :, :, 1] < 80)
        ink = (dark | red).astype(np.float32)
        glyph = np.stack([cv2.resize(cell, (16, 24), interpolation=cv2.INTER_AREA) for cell in ink])
        return self.observation(glyph)


def templateDistance(observations, templates):
    return np.min(np.stack([np.mean(abs(observations[:, None] - np.roll(templates, shift, axis=2)[None]), axis=(2, 3))
                            for shift in (-1, 0, 1)]), axis=(0, 2))


class MoveTracker:
    def __init__(self, adapter, firstObservation):
        self.board = shogi.Board()
        self.adapter = adapter
        adapter.initialize(firstObservation, self.board)
        self.templates = {}
        codes = boardCodes(self.board)
        for code in set(codes) - {0}:
            self.templates[code] = firstObservation['glyph'][codes == code].copy()
        self.moves = []
        self.overlayFrames = 0
        self.unmatchedFrames = 0
        self.pending = None
        self.pendingCount = 0
        self.refreshMoves()

    def refreshMoves(self):
        self.codes = boardCodes(self.board)
        self.options = []
        for move in self.board.legal_moves:
            pieceType = move.drop_piece_type if move.from_square is None else self.board.piece_type_at(move.from_square)
            if move.promotion:
                pieceType = shogi.PIECE_PROMOTED[pieceType]
            self.options.append((move, self.board.turn * 14 + pieceType))

    def observe(self, observation, timestamp):
        costs = self.adapter.costs(observation, self.templates)
        occupancy = observation['occupancy']
        currentScore = float(costs[np.arange(81), self.codes].sum())
        options = []
        for move, code in self.options:
            destination = move.to_square
            score = currentScore - costs[destination, self.codes[destination]] + costs[destination, code]
            if move.from_square is None:
                # 木目盤の二文字駒では字形に加え、空マスに現れた駒も打ちの根拠にする。
                score -= getattr(self.adapter, 'dropEvidenceWeight', 0) * occupancy[destination]
            if move.from_square is not None:
                source = move.from_square
                score += costs[source, 0] - costs[source, self.codes[source]]
            options.append((float(score), move, code))
        if not options:
            return
        options.sort(key=lambda option: option[0])
        bestScore, move, code = options[0]
        margin = options[1][0] - bestScore if len(options) > 1 else 10
        gain = currentScore - bestScore
        # 終局画面・フェードなど、盤全体の見た目が変わるフレームは採用しない。
        if bestScore / 81 > .20:
            self.overlayFrames += 1
            self.pending = None
            self.pendingCount = 0
            return
        sourceOccupancy = 0 if move.from_square is None else occupancy[move.from_square]
        if gain < .55 or margin < .035 or sourceOccupancy > .35 or occupancy[move.to_square] < .6:
            if gain > .55:
                self.unmatchedFrames += 1
            self.pending = None
            self.pendingCount = 0
            return
        if move.from_square is None and margin < .10:
            self.pending = None
            self.pendingCount = 0
            return
        usi = move.usi()
        if self.pending == usi:
            self.pendingCount += 1
        else:
            self.pending, self.pendingCount = usi, 1
        # 光や王手表示で打った駒が一瞬だけ別種に見えるため、連続一致を待つ。
        confirmations = 3 if move.from_square is None else getattr(self.adapter, 'normalConfirmations', 1)
        if self.pendingCount < confirmations:
            return
        label = shogi.KIF.Exporter.kif_move_from(usi, self.board)
        review = margin < .18 or bestScore / 81 > .15 or code not in self.templates
        if code not in self.templates and costs[move.to_square, code] < .8:
            self.templates[code] = observation['glyph'][move.to_square:move.to_square + 1].copy()
        self.board.push(move)
        self.moves.append({'number': len(self.moves) + 1, 'usi': usi, 'label': label,
                           'time': round(timestamp, 3), 'sfen': self.board.sfen(),
                           'score': round(margin, 4), 'review': bool(review), 'edited': False})
        self.pending, self.pendingCount = None, 0
        self.refreshMoves()
        return True


class FrameRecognizer:
    """動画の読み込みと分離し、PCとブラウザーで同じフレーム認識を使う。"""
    def __init__(self, settings, firstFrame):
        self.settings = settings
        self.adapter = {'kioh': KiohAdapter, 'wars': WarsAdapter, 'generic': GenericAdapter}[settings['profile']]()
        observation = self.adapter.features(extractCells(firstFrame, settings['board'], settings['orientation']))
        self.tracker = MoveTracker(self.adapter, observation)
        self.previousObservation = None
        self.lastAccepted = False
        self.analyzedFrames = 0
        self.lastTime = settings['startTime']

    def feed(self, frame, timestamp):
        if timestamp <= self.lastTime:
            return
        observation = self.adapter.features(extractCells(frame, self.settings['board'], self.settings['orientation']))
        tracker = self.tracker
        changed = self.previousObservation is None or self.lastAccepted or tracker.pending is not None
        if not changed:
            glyphChange = np.mean(abs(observation['glyph'] - self.previousObservation['glyph']), axis=(1, 2)).max()
            occupancyChange = abs(observation['occupancy'] - self.previousObservation['occupancy']).max()
            directionChange = abs(observation['direction'] - self.previousObservation['direction']).max()
            changed = glyphChange > .012 or occupancyChange > .06 or directionChange > .06
        if changed:
            self.lastAccepted = bool(tracker.observe(observation, timestamp))
            self.previousObservation = {key: value.copy() for key, value in observation.items()}
        self.analyzedFrames += 1
        self.lastTime = timestamp

    def result(self, elapsedSeconds=0, cancelled=False):
        tracker = self.tracker
        warnings = []
        moves = [dict(row) for row in tracker.moves]
        if self.settings['profile'] == 'generic':
            warnings.append('他アプリの認識は試験対応です。全手を映像と照合してください。')
            for row in moves:
                row['review'] = True
        if tracker.overlayFrames:
            warnings.append(f'演出・遮蔽など盤面と一致しない {tracker.overlayFrames} フレームを除外しました。')
        if tracker.unmatchedFrames:
            warnings.append('候補が決まらないフレームがありました。抜けた手や誤認がないか確認してください。')
        if not moves:
            warnings.append('指し手を抽出できませんでした。開始時刻・範囲・駒の見た目を確認してください。')
        warnings.append('投了・時間切れなどの終局理由は自動判定していません。')
        return {'moves': moves, 'warnings': warnings, 'analyzedFrames': self.analyzedFrames,
                'elapsedSeconds': round(elapsedSeconds, 2), 'lastTime': round(self.lastTime, 6),
                'cancelled': cancelled, 'engineVersion': '1.1', 'source': 'video-only'}

    def checkpoint(self):
        tracker = self.tracker
        return {'version': 1, 'settings': self.settings,
                'templates': {str(code): value.tolist() for code, value in tracker.templates.items()},
                'directions': {str(code): float(value) for code, value in getattr(self.adapter, 'directions', {}).items()},
                'emptyTemplates': getattr(self.adapter, 'emptyTemplates', np.array([])).tolist(),
                'moves': tracker.moves, 'overlayFrames': tracker.overlayFrames,
                'unmatchedFrames': tracker.unmatchedFrames, 'pending': tracker.pending, 'pendingCount': tracker.pendingCount,
                'previousObservation': None if self.previousObservation is None else
                    {key: value.tolist() for key, value in self.previousObservation.items()},
                'lastAccepted': self.lastAccepted, 'analyzedFrames': self.analyzedFrames, 'lastTime': self.lastTime}

    @classmethod
    def restore(cls, checkpoint):
        if checkpoint.get('version') != 1:
            raise ValueError('途中保存の形式が対応していません。最初から解析してください。')
        instance = cls.__new__(cls)
        instance.settings = checkpoint['settings']
        instance.adapter = {'kioh': KiohAdapter, 'wars': WarsAdapter, 'generic': GenericAdapter}[instance.settings['profile']]()
        if checkpoint['directions']:
            instance.adapter.directions = {int(code): np.float32(value) for code, value in checkpoint['directions'].items()}
        if checkpoint['emptyTemplates']:
            instance.adapter.emptyTemplates = np.array(checkpoint['emptyTemplates'], dtype=np.float32)
        tracker = MoveTracker.__new__(MoveTracker)
        tracker.board = shogi.Board()
        tracker.moves = [dict(row) for row in checkpoint['moves']]
        for row in tracker.moves:
            move = shogi.Move.from_usi(row['usi'])
            if not tracker.board.is_legal(move):
                raise ValueError('途中保存の指し手が壊れています。最初から解析してください。')
            tracker.board.push(move)
        tracker.adapter = instance.adapter
        tracker.templates = {int(code): np.array(value, dtype=np.float32) for code, value in checkpoint['templates'].items()}
        for key in ('overlayFrames', 'unmatchedFrames', 'pending', 'pendingCount'):
            setattr(tracker, key, checkpoint[key])
        tracker.refreshMoves()
        instance.tracker = tracker
        previous = checkpoint['previousObservation']
        instance.previousObservation = None if previous is None else {key: np.array(value, dtype=np.float32) for key, value in previous.items()}
        for key in ('lastAccepted', 'analyzedFrames', 'lastTime'):
            setattr(instance, key, checkpoint[key])
        return instance


def analyzeVideo(path, settings, onProgress=None, isCancelled=None):
    onProgress = onProgress or (lambda value: None)
    isCancelled = isCancelled or (lambda: False)
    capture = cv2.VideoCapture(str(path))
    startedAt = monotonic()
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frameCount = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or frameCount <= 0:
            raise ValueError('動画を読み取れません。')
        startTime = settings['startTime']
        endTime = min(settings.get('endTime') or frameCount / fps, frameCount / fps)
        capture.set(cv2.CAP_PROP_POS_MSEC, startTime * 1000)
        ok, frame = capture.read()
        if not ok:
            raise ValueError('開始フレームを読み取れません。')
        recognizer = FrameRecognizer(settings, frame)
        tracker = recognizer.tracker
        sampleInterval = 1 / min(settings['sampleFps'], fps)
        nextSample = startTime + sampleInterval
        lastProgress = monotonic()
        analyzedFrames, timestamp = 0, startTime
        while not isCancelled():
            if not capture.grab():
                if timestamp < endTime - max(1, 2 / fps):
                    raise ValueError(f'{timestamp:.1f}秒で動画のデコードが途切れました。動画を変換して再試行してください。')
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if timestamp <= 0:
                timestamp = capture.get(cv2.CAP_PROP_POS_FRAMES) / fps
            if timestamp > endTime:
                break
            if timestamp + .0001 < nextSample:
                continue
            nextSample = timestamp + sampleInterval - .5 / fps
            ok, frame = capture.retrieve()
            if not ok:
                raise ValueError(f'{timestamp:.1f}秒の画像を取り出せません。')
            recognizer.feed(frame, timestamp)
            analyzedFrames += 1
            if monotonic() - lastProgress > .5:
                onProgress({'progress': min(.999, (timestamp - startTime) / (endTime - startTime)),
                            'time': round(timestamp, 2), 'moveCount': len(tracker.moves),
                            'message': f'{len(tracker.moves)}手を抽出しています'})
                lastProgress = monotonic()
        result = recognizer.result(monotonic() - startedAt, isCancelled())
        result['lastTime'] = round(timestamp, 3)
        return result
    finally:
        capture.release()
