"""棋譜を厳密に読み、合法性を確認して書き出す。"""
import re
import shogi
import shogi.KIF

terminations = {'投了', '中断', '千日手', '持将棋', '詰み', '切れ負け', '反則勝ち', '反則負け', '入玉勝ち'}


def decodeRecord(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('棋譜の文字コードを読めません。UTF-8 または Shift-JIS で保存してください。')


def replayMoves(moves: list[str]) -> list[dict]:
    board = shogi.Board()
    rows = []
    for index, usi in enumerate(moves):
        legalMoves = {move.usi() for move in board.legal_moves}
        if usi not in legalMoves:
            raise ValueError(f'{index + 1}手目の {usi} は、この局面の合法手ではありません。')
        label = shogi.KIF.Exporter.kif_move_from(usi, board)
        board.push_usi(usi)
        rows.append({'number': index + 1, 'usi': usi, 'label': label, 'sfen': board.sfen()})
    return rows


def parseRecord(text: str) -> dict:
    if re.search(r'手合割[：:]\s*(?!平手)[^\s]+', text):
        raise ValueError('現在の自動認識・比較は平手に対応しています。')
    if '後手番' in text or '先手の持駒' in text or re.search(r'^\|', text, re.M):
        raise ValueError('途中局面・駒落ちの棋譜は現在の比較対象にできません。')
    moves, names, lastSquare, termination = [], ['先手', '後手'], None, None
    board = shogi.Board()
    for lineNumber, line in enumerate(text.splitlines(), 1):
        for color, key in enumerate(('先手', '後手')):
            match = re.match(rf'^{key}[：:]\s*(.*)$', line)
            if match:
                names[color] = match.group(1).strip()[:100]
        match = re.match(r'^\s*(\d+)\s+(.+)$', line)
        if not match:
            continue
        number, body = int(match.group(1)), match.group(2).strip()
        if termination:
            raise ValueError(f'{lineNumber}行目：終局後の指し手・変化手順には対応していません。')
        if number != len(moves) + 1:
            raise ValueError(f'{lineNumber}行目：手数が連続していません。')
        timeSuffix = r'(?:\s*\(\s*\d+:\d+\s*/\s*\d+:\d+:\d+\s*\))?\s*'
        end = next((value for value in terminations if re.fullmatch(re.escape(value) + timeSuffix, body)), None)
        if end:
            termination = end
            continue
        normalized = re.sub(r'同[\s\u3000]*', '同　', line)
        try:
            usi, lastSquare, _ = shogi.KIF.Parser.parse_move_str(normalized, lastSquare)
        except (ValueError, TypeError, IndexError) as exc:
            raise ValueError(f'{lineNumber}行目の指し手を読めません。') from exc
        if not usi:
            raise ValueError(f'{lineNumber}行目の指し手を読めません：{body}')
        if usi not in {move.usi() for move in board.legal_moves}:
            raise ValueError(f'{number}手目の {usi} が合法手ではありません。')
        notation = body.replace('王', '玉').replace('竜', '龍').replace('成銀', '全').replace('成桂', '圭').replace('成香', '杏')
        token = re.fullmatch(r'(?:[１２３４５６７８９][一二三四五六七八九]|同\s*)'
                             r'([歩香桂銀金角飛玉と杏圭全馬龍])(不成|成|打)?(?:\(([0-9]{2})\))?' + timeSuffix, notation)
        move = shogi.Move.from_usi(usi)
        pieceType = move.drop_piece_type if move.from_square is None else board.piece_type_at(move.from_square)
        if not token or token[1] != shogi.PIECE_JAPANESE_SYMBOLS[pieceType]:
            raise ValueError(f'{number}手目の駒の種類・表記が局面と一致しません。')
        if move.from_square is None and token[3] not in (None, '00'):
            raise ValueError(f'{number}手目の駒打ちに不正な移動元が指定されています。')
        board.push_usi(usi)
        moves.append(usi)
    if not moves:
        raise ValueError('指し手が見つかりません。KIF形式の棋譜を選んでください。')
    return {'moves': moves, 'names': names, 'termination': termination, 'rows': replayMoves(moves)}


def exportRecord(moves: list[str], names: list[str] | None = None, termination: str | None = None) -> str:
    names = names or ['先手', '後手']
    cleanNames = [re.sub(r'[\r\n]', '', name)[:100] for name in names]
    rows = replayMoves(moves)
    lines = ['# ---- KIF形式 ----', '手合割：平手', f'先手：{cleanNames[0]}', f'後手：{cleanNames[1]}',
             '* 動画からの推定棋譜です。映像との照合を行ってください。', '手数----指手---------消費時間--']
    lines += [f'{row["number"]:4d} {row["label"]}' for row in rows]
    if termination:
        if termination not in terminations:
            raise ValueError('不明な終局理由です。')
        lines.append(f'{len(rows) + 1:4d} {termination}')
    else:
        lines.append('* 終局理由は未確認です。')
    return '\r\n'.join(lines) + '\r\n'


def compareMoves(predicted: list[str], reference: list[str]) -> dict:
    prefix = 0
    for actual, expected in zip(predicted, reference):
        if actual != expected:
            break
        prefix += 1
    positional = sum(a == b for a, b in zip(predicted, reference))
    return {'referenceCount': len(reference), 'predictedCount': len(predicted), 'prefixMatch': prefix,
            'positionMatches': positional, 'exactMatch': predicted == reference,
            'firstMismatch': None if predicted == reference else prefix + 1}
