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


class MotionSampler:
    """各フレームの小さな盤画像で変化を見つけ、動いた区間は間引かない。"""
    def __init__(self, settings, fps):
        self.box = settings['board']
        self.interval = 1 / min(settings['sampleFps'], fps)
        self.tolerance = .5 / fps
        self.nextSample = settings['startTime'] + self.interval
        self.previous = None
        self.settleFrames = 0

    def shouldRead(self, frame, timestamp):
        height, width = frame.shape[:2]
        box = self.box
        left, top = round(box['x'] * width), round(box['y'] * height)
        right, bottom = round((box['x'] + box['width']) * width), round((box['y'] + box['height']) * height)
        probe = cv2.resize(frame[top:bottom, left:right], (72, 72), interpolation=cv2.INTER_AREA).astype(np.int16)
        changed = self.previous is None or np.abs(probe - self.previous).reshape(9, 8, 9, 8, 3).mean(axis=(1, 3, 4)).max() > 3
        if not changed and not self.settleFrames and timestamp + .0001 < self.nextSample:
            return False
        self.previous = probe
        self.settleFrames = 3 if changed else max(0, self.settleFrames - 1)
        self.nextSample = timestamp + self.interval - self.tolerance
        return True


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
        # 初回のoccupancyは未校正なので、空マスの見本を作ってから再判定する。
        occupancy = self.observation(observation['glyph'])['occupancy']
        if np.any(occupancy[codes != 0] < .35):
            raise ValueError('平手の初期配置にあるはずの駒が見つかりません。1手目が動く前へ開始時刻を戻し、盤面の枠も確認してください。')
        # 枠が広すぎると駒が別のマスへはみ出す。同色・同種の駒字の整合性で検出する。
        distances = []
        for code in set(codes) - {0}:
            group = observation['glyph'][codes == code]
            if len(group) > 1:
                for index in range(len(group)):
                    distances.append(templateDistance(group[index:index + 1], np.delete(group, index, axis=0))[0])
        if np.median(distances) > .10:
            raise ValueError('駒字がマスに収まっていません。緑の線を盤の9×9マスに重ね、数字・外枠・駒台を含めずに範囲を合わせてください。')

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
        self.frameUsable = True
        self.pendingOccupancy = None
        self.pendingCosts = None
        self.provisionalDrop = None
        self.firstMoverMismatch = 0
        self.firstMoverSource = None
        self.refreshMoves()

    def refreshMoves(self):
        self.codes = boardCodes(self.board)
        self.options = []
        for move in self.board.legal_moves:
            pieceType = move.drop_piece_type if move.from_square is None else self.board.piece_type_at(move.from_square)
            if move.promotion:
                pieceType = shogi.PIECE_PROMOTED[pieceType]
            self.options.append((move, self.board.turn * 14 + pieceType))

    def hasReplyEvidence(self, move, costs, occupancy):
        """打ちが二枚に写り、次の合法手も一意に写っている場合だけ確認を補う。"""
        self.board.push(move)
        try:
            codes = boardCodes(self.board)
            currentScore = float(costs[np.arange(81), codes].sum())
            replies = []
            for reply in self.board.legal_moves:
                pieceType = reply.drop_piece_type if reply.from_square is None else self.board.piece_type_at(reply.from_square)
                if reply.promotion:
                    pieceType = shogi.PIECE_PROMOTED[pieceType]
                code = self.board.turn * 14 + pieceType
                projected = codes.copy()
                if reply.from_square is not None:
                    projected[reply.from_square] = 0
                projected[reply.to_square] = code
                score = float(costs[np.arange(81), projected].sum())
                replies.append((score, reply, projected, code))
            replies.sort(key=lambda value: value[0])
            if not replies:
                return False
            score, reply, projected, code = replies[0]
            if reply.from_square is None or reply.promotion or self.pendingOccupancy is None:
                return None
            margin = replies[1][0] - score if len(replies) > 1 else 10
            mismatch = ((occupancy > .6) & (projected == 0)) | ((occupancy < .35) & (projected != 0))
            supported = (currentScore - score > .75 and margin > .18 and score / 81 < .12 and
                         self.pendingOccupancy[reply.from_square] > .6 and
                         self.pendingCosts[reply.to_square, code] - costs[reply.to_square, code] > .2 and
                         occupancy[reply.from_square] < .25 and occupancy[reply.to_square] > .6 and not mismatch.any())
            return reply.usi() if supported else None
        finally:
            self.board.pop()

    def checkFirstMover(self, costs, occupancy, currentScore, ownBestScore):
        if self.moves or self.board.move_number != 1 or not isinstance(self.adapter, WarsAdapter):
            return
        opposite = shogi.Board()
        opposite.turn = shogi.WHITE
        candidates = []
        for move in opposite.legal_moves:
            projected = self.codes.copy()
            projected[move.from_square] = 0
            projected[move.to_square] = 14 + opposite.piece_type_at(move.from_square)
            candidates.append((float(costs[np.arange(81), projected].sum()), move, projected))
        candidates.sort(key=lambda value: value[0])
        score, move, projected = candidates[0]
        mismatch = ((occupancy > .6) & (projected == 0)) | ((occupancy < .35) & (projected != 0))
        missing = np.flatnonzero((occupancy < .25) & (self.codes != 0))
        # 移動先が発光中でも、最初に相手の歩だけが元のマスから消えれば向きを確認できる。
        oppositePawnLeft = (missing.size == 1 and self.codes[missing[0]] == 14 + shogi.PAWN and
                            costs[missing[0], 0] < .15 and currentScore / 81 < .08 and
                            np.count_nonzero((occupancy > .6) & (self.codes == 0)) <= 1)
        oppositeMoved = oppositePawnLeft or (currentScore - score > .55 and ownBestScore - score > .3 and
                         candidates[1][0] - score > .18 and score / 81 < .12 and
                         occupancy[move.from_square] < .25 and occupancy[move.to_square] > .6 and not mismatch.any())
        source = int(missing[0]) if oppositePawnLeft else move.from_square if oppositeMoved else None
        self.firstMoverMismatch = (self.firstMoverMismatch + 1 if source == self.firstMoverSource else 1) if source is not None else 0
        self.firstMoverSource = source
        if self.firstMoverMismatch >= 2:
            raise ValueError('最初に動いた駒が先後の設定と一致しません。「画面の下側」の先手・後手を切り替え、1手目が動く前から解析してください。')

    def observe(self, observation, timestamp):
        costs = self.adapter.costs(observation, self.templates)
        occupancy = observation['occupancy']
        if self.provisionalDrop is not None and timestamp > self.provisionalDrop['time']:
            before = boardCodes(shogi.Board(self.provisionalDrop['boardBefore']))
            mismatch = ((occupancy > .6) & (before == 0)) | ((occupancy < .35) & (before != 0))
            beforeScore = float(costs[np.arange(81), before].sum())
            afterScore = float(costs[np.arange(81), self.codes].sum())
            dropSquare = shogi.Move.from_usi(self.moves[self.provisionalDrop['moveCount']]['usi']).to_square
            dropGain = beforeScore - afterScore + getattr(self.adapter, 'dropEvidenceWeight', 0) * occupancy[dropSquare]
            afterMismatch = ((occupancy > .6) & (self.codes == 0)) | ((occupancy < .35) & (self.codes != 0))
            if not mismatch.any() and beforeScore / 81 < .12 and beforeScore < afterScore:
                # 応手らしい演出が元局面へ戻ったら、未確定の打ちを破棄する。
                count = self.provisionalDrop['moveCount']
                while len(self.moves) > count:
                    self.board.pop()
                    self.moves.pop()
                self.provisionalDrop = None
                self.pending, self.pendingCount = None, 0
                self.refreshMoves()
            elif not afterMismatch.any() and afterScore / 81 < .12 and dropGain > .55:
                # 打ちだけの局面を三枚目で確認できれば、誤った応手の仮説を解除する。
                self.provisionalDrop = None
                self.pending, self.pendingCount = None, 0
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
        self.checkFirstMover(costs, occupancy, currentScore, bestScore)
        margin = options[1][0] - bestScore if len(options) > 1 else 10
        gain = currentScore - bestScore
        # 終局画面・フェードなど、盤全体の見た目が変わるフレームは採用しない。
        if bestScore / 81 > .20:
            self.frameUsable = False
            self.overlayFrames += 1
            self.pending = None
            self.pendingCount = 0
            return
        self.frameUsable = True
        if self.provisionalDrop is not None and move.usi() != self.provisionalDrop['reply']:
            self.pending, self.pendingCount = None, 0
            return
        sourceOccupancy = 0 if move.from_square is None else occupancy[move.from_square]
        if gain < .55 or margin < .035 or sourceOccupancy > .35 or occupancy[move.to_square] < .6:
            occupancyMismatch = ((occupancy > .6) & (self.codes == 0)) | ((occupancy < .35) & (self.codes != 0))
            if gain > .55 or np.count_nonzero(occupancyMismatch) >= 2:
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
            self.pendingOccupancy = occupancy.copy()
            self.pendingCosts = costs.copy()
        # 光や王手表示で打った駒が一瞬だけ別種に見えるため、連続一致を待つ。
        confirmations = 3 if move.from_square is None else getattr(self.adapter, 'normalConfirmations', 1)
        replyConfirmed = (isinstance(self.adapter, WarsAdapter) and move.from_square is None and self.pendingCount == 2 and
                          self.hasReplyEvidence(move, costs, occupancy))
        if self.pendingCount < confirmations and not replyConfirmed:
            return
        label = shogi.KIF.Exporter.kif_move_from(usi, self.board)
        review = bool(replyConfirmed) or margin < .18 or bestScore / 81 > .15 or code not in self.templates
        if code not in self.templates and costs[move.to_square, code] < .8:
            self.templates[code] = observation['glyph'][move.to_square:move.to_square + 1].copy()
        if replyConfirmed:
            self.provisionalDrop = {'boardBefore': self.board.sfen(), 'moveCount': len(self.moves), 'time': timestamp, 'reply': replyConfirmed}
        elif self.provisionalDrop is not None and timestamp > self.provisionalDrop['time']:
            self.provisionalDrop = None
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
        self.mismatchFrames = 0
        self.trackingLost = False

    def feed(self, frame, timestamp):
        if timestamp <= self.lastTime:
            return
        observation = self.adapter.features(extractCells(frame, self.settings['board'], self.settings['orientation']))
        tracker = self.tracker
        changed = self.previousObservation is None or self.lastAccepted or tracker.pending is not None or tracker.firstMoverMismatch > 0
        if not changed:
            glyphChange = np.mean(abs(observation['glyph'] - self.previousObservation['glyph']), axis=(1, 2)).max()
            occupancyChange = abs(observation['occupancy'] - self.previousObservation['occupancy']).max()
            directionChange = abs(observation['direction'] - self.previousObservation['direction']).max()
            changed = glyphChange > .012 or occupancyChange > .06 or directionChange > .06
        if changed:
            self.lastAccepted = False
            # 直前の手を確定した画像に次の手も写っていれば、その候補も確認する。
            # 同じ局面を同一フレームで二度確認せず、演出を除く連続一致を維持する。
            for _ in range(4 if isinstance(self.adapter, WarsAdapter) else 1):
                if not tracker.observe(observation, timestamp):
                    break
                self.lastAccepted = True
            self.previousObservation = {key: value.copy() for key, value in observation.items()}
        occupancy = observation['occupancy']
        missing = np.count_nonzero((occupancy < .35) & (tracker.codes != 0))
        extra = np.count_nonzero((occupancy > .6) & (tracker.codes == 0))
        mismatch = missing + extra
        # 多くの手を見失った場合も警告する。盤全体が消えるだけの画面は除く。
        inconsistent = 2 <= mismatch <= 12 or (missing >= 2 and extra >= 2)
        self.mismatchFrames = self.mismatchFrames + 1 if tracker.frameUsable and tracker.pending is None and inconsistent else 0
        if self.mismatchFrames >= 3:
            self.trackingLost = True
        elif tracker.frameUsable and mismatch == 0:
            self.trackingLost = False
        self.analyzedFrames += 1
        self.lastTime = timestamp

    def result(self, elapsedSeconds=0, cancelled=False):
        tracker = self.tracker
        warnings = []
        moves = [dict(row) for row in tracker.moves[:self.moveCount()]]
        incomplete = self.trackingLost or tracker.provisionalDrop is not None or (not cancelled and tracker.pending is not None)
        if incomplete:
            warnings.append(f'{len(moves)}手以降で映像の局面に追従できていない可能性があります。開始位置・盤の範囲を確認して最初から解析し、残る場合は動画を提供してください。')
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
                'cancelled': cancelled, 'recognitionIncomplete': incomplete, 'engineVersion': '1.3.0', 'source': 'video-only'}

    def moveCount(self):
        provisional = self.tracker.provisionalDrop
        return provisional['moveCount'] if provisional is not None else len(self.tracker.moves)

    def checkpoint(self):
        tracker = self.tracker
        return {'version': 1, 'settings': self.settings,
                'templates': {str(code): value.tolist() for code, value in tracker.templates.items()},
                'directions': {str(code): float(value) for code, value in getattr(self.adapter, 'directions', {}).items()},
                'emptyTemplates': getattr(self.adapter, 'emptyTemplates', np.array([])).tolist(),
                'moves': tracker.moves, 'overlayFrames': tracker.overlayFrames,
                'unmatchedFrames': tracker.unmatchedFrames, 'pending': tracker.pending, 'pendingCount': tracker.pendingCount,
                'pendingOccupancy': tracker.pendingOccupancy.tolist() if tracker.pending is not None and tracker.pendingOccupancy is not None else None,
                'pendingCosts': tracker.pendingCosts.tolist() if tracker.pending is not None and tracker.pendingCosts is not None else None,
                'provisionalDrop': tracker.provisionalDrop,
                'firstMoverMismatch': tracker.firstMoverMismatch,
                'firstMoverSource': tracker.firstMoverSource,
                'previousObservation': None if self.previousObservation is None else
                    {key: value.tolist() for key, value in self.previousObservation.items()},
                'lastAccepted': self.lastAccepted, 'mismatchFrames': self.mismatchFrames, 'trackingLost': self.trackingLost, 'frameUsable': tracker.frameUsable,
                'analyzedFrames': self.analyzedFrames, 'lastTime': self.lastTime}

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
        tracker.frameUsable = checkpoint.get('frameUsable', True)
        tracker.pendingOccupancy = None if checkpoint.get('pendingOccupancy') is None else np.array(checkpoint['pendingOccupancy'], dtype=np.float32)
        tracker.pendingCosts = None if checkpoint.get('pendingCosts') is None else np.array(checkpoint['pendingCosts'], dtype=np.float32)
        tracker.provisionalDrop = checkpoint.get('provisionalDrop')
        tracker.firstMoverMismatch = checkpoint.get('firstMoverMismatch', 0)
        tracker.firstMoverSource = checkpoint.get('firstMoverSource')
        instance.tracker = tracker
        previous = checkpoint['previousObservation']
        instance.previousObservation = None if previous is None else {key: np.array(value, dtype=np.float32) for key, value in previous.items()}
        for key in ('lastAccepted', 'analyzedFrames', 'lastTime'):
            setattr(instance, key, checkpoint[key])
        instance.mismatchFrames = checkpoint.get('mismatchFrames', 0)
        instance.trackingLost = checkpoint.get('trackingLost', instance.mismatchFrames >= 3)
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
        sampler = MotionSampler(settings, fps) if settings['profile'] == 'wars' else None
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
            if sampler is None and timestamp + .0001 < nextSample:
                continue
            nextSample = timestamp + sampleInterval - .5 / fps
            ok, frame = capture.retrieve()
            if not ok:
                raise ValueError(f'{timestamp:.1f}秒の画像を取り出せません。')
            if sampler is not None and not sampler.shouldRead(frame, timestamp):
                continue
            recognizer.feed(frame, timestamp)
            analyzedFrames += 1
            if monotonic() - lastProgress > .5:
                onProgress({'progress': min(.999, (timestamp - startTime) / (endTime - startTime)),
                            'time': round(timestamp, 2), 'moveCount': recognizer.moveCount(),
                            'message': f'{recognizer.moveCount()}手を抽出しています'})
                lastProgress = monotonic()
        result = recognizer.result(monotonic() - startedAt, isCancelled())
        result['lastTime'] = round(timestamp, 3)
        return result
    finally:
        capture.release()
