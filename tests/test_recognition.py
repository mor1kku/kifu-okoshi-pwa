import cv2
import numpy as np
import pytest
import shogi
from pathlib import Path
from backend.recognition import GenericAdapter, WarsAdapter, MoveTracker, analyzeVideo, extractCells


def renderBoard(board):
    image = np.full((438, 396, 3), 225, dtype=np.uint8)
    for square in range(81):
        row, column = divmod(square, 9)
        x, y = round((column + .5) * 44), round((row + .5) * 438 / 9)
        piece = board.piece_at(square)
        if piece:
            cell = np.full((42, 38, 3), 225, dtype=np.uint8)
            symbol = shogi.PIECE_SYMBOLS[piece.piece_type].replace('+', '').upper()
            cv2.putText(cell, symbol, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, .6, (20, 20, 20), 2, cv2.LINE_AA)
            if piece.color == shogi.WHITE:
                cell = cv2.rotate(cell, cv2.ROTATE_180)
            image[round(y - 21):round(y + 21), x - 19:x + 19] = cell
    return image


def makeVideo(path, holdFrames, rotated=False):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (396, 438))
    assert writer.isOpened()
    moves = ['7g7f', '3c3d', '2g2f', '8c8d', '2f2e', '8d8e']
    board = shogi.Board()
    for usi in [None] + moves:
        if usi:
            board.push_usi(usi)
        frame = renderBoard(board)
        if rotated:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        for _ in range(holdFrames):
            writer.write(frame)
    writer.release()
    return moves


@pytest.mark.parametrize('holdFrames,orientation,profile', [(4, 'sente', 'generic'), (30, 'sente', 'generic'), (8, 'gote', 'generic'), (4, 'sente', 'wars'), (30, 'gote', 'wars')])
def testVariableSpeedAndOrientation(tmp_path, holdFrames, orientation, profile):
    path = tmp_path / 'game.mp4'
    expected = makeVideo(path, holdFrames, orientation == 'gote')
    result = analyzeVideo(path, {'startTime': 0, 'endTime': None, 'sampleFps': 30,
                                'profile': profile, 'orientation': orientation,
                                'board': {'x': 0, 'y': 0, 'width': 1, 'height': 1}})
    assert [move['usi'] for move in result['moves']] == expected
    if profile == 'generic':
        assert all(move['review'] for move in result['moves'])


def testCancellationKeepsPartialResult(tmp_path):
    path = tmp_path / 'game.mp4'
    makeVideo(path, 10)
    result = analyzeVideo(path, {'startTime': 0, 'sampleFps': 12, 'profile': 'generic',
                                'orientation': 'sente', 'board': {'x': 0, 'y': 0, 'width': 1, 'height': 1}},
                          isCancelled=lambda: True)
    assert result['cancelled']
    assert result['moves'] == []


def testBlankStartingFrameIsRejected():
    adapter = GenericAdapter()
    image = np.full((438, 396, 3), 225, dtype=np.uint8)
    features = adapter.features(extractCells(image, {'x': 0, 'y': 0, 'width': 1, 'height': 1}))
    with pytest.raises(ValueError):
        MoveTracker(adapter, features)


def testCostsDoNotMutateFeatures():
    adapter = GenericAdapter()
    features = adapter.features(extractCells(renderBoard(shogi.Board()), {'x': 0, 'y': 0, 'width': 1, 'height': 1}))
    tracker = MoveTracker(adapter, features)
    before = {key: value.copy() for key, value in features.items()}
    adapter.costs(features, tracker.templates)
    for key in before:
        np.testing.assert_array_equal(before[key], features[key])


def testWarsWoodBackgroundIsEmpty():
    cells = np.full((81, 42, 38, 3), (85, 155, 240), dtype=np.uint8)
    cells[:, ::3, :, :] = (60, 130, 210)
    assert WarsAdapter().features(cells)['glyph'].max() == 0


@pytest.mark.skipif(not (Path(__file__).parent / 'fixtures' / 'wars-silver-drop.npz').exists(), reason='実動画由来の非公開fixtureが必要です')
def testWarsSilverDropWithRookStillAtSource():
    # 実動画の初期駒字と最終フレームの文字マスク。盤外の情報は含まない。
    with np.load(Path(__file__).parent / 'fixtures' / 'wars-silver-drop.npz') as fixture:
        adapter = WarsAdapter()
        tracker = MoveTracker(adapter, adapter.observation(fixture['initial']))
        tracker.board = shogi.Board(str(fixture['sfen']))
        tracker.refreshMoves()
        for time in [224, 224.1, 224.2]:
            tracker.observe(adapter.observation(fixture['final']), time)
    assert [row['usi'] for row in tracker.moves] == ['S*2g']


def testWarsTransientMoveDoesNotBecomeARecord():
    adapter = WarsAdapter()
    box = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
    board = shogi.Board()
    tracker = MoveTracker(adapter, adapter.features(extractCells(renderBoard(board), box)))
    board.push_usi('7g7f')
    tracker.observe(adapter.features(extractCells(renderBoard(board), box)), .1)
    assert tracker.moves == []
    tracker.observe(adapter.features(extractCells(renderBoard(shogi.Board()), box)), .2)
    assert tracker.moves == []


@pytest.mark.skipif(not (Path(__file__).parent / 'fixtures' / 'wars-silver-drop.npz').exists(), reason='実動画由来の非公開fixtureが必要です')
def testWarsAnimationCannotCreateARookMove():
    with np.load(Path(__file__).parent / 'fixtures' / 'wars-silver-drop.npz') as fixture:
        adapter = WarsAdapter()
        tracker = MoveTracker(adapter, adapter.observation(fixture['initial']))
        tracker.board = shogi.Board(str(fixture['beforeFlightSfen']))
        tracker.refreshMoves()
        for time in [212, 212.1, 212.2]:
            tracker.observe(adapter.observation(fixture['inFlight']), time)
    assert tracker.moves == []
