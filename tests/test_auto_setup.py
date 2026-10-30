import cv2
import numpy as np
import pytest
import shogi
from backend.auto_setup import autoBoard, boxOverlap, GameScanner
from tests.test_recognition import renderBoard


def scene(board=None, reverse=False, offset=(40, 80)):
    image = np.full((800, 600, 3), 245, dtype=np.uint8)
    if board is None:
        return image
    crop = renderBoard(board)
    if reverse:
        crop = cv2.rotate(crop, cv2.ROTATE_180)
    for index in range(10):
        x, y = min(395, round(index*44)), min(437, round(index*438/9))
        cv2.line(crop, (x, 0), (x, 437), (125, 125, 125), 1)
        cv2.line(crop, (0, y), (395, y), (125, 125, 125), 1)
    x, y = offset
    image[y:y+438, x:x+396] = crop
    return image


def testBoardDetectionAndIncorrectHint():
    expected = {'x': 40/600, 'y': 80/800, 'width': 396/600, 'height': 438/800}
    found = autoBoard(scene(shogi.Board()), 'wars', {'x': .3, 'y': .3, 'width': .6, 'height': .6})
    assert boxOverlap(found['board'], expected) > .97
    with pytest.raises(ValueError, match='初期配置'):
        autoBoard(scene(), 'wars')


@pytest.mark.parametrize('nextOffset', [(100, 220), (84, 80)])
def testTwoGamesWithDifferentPositionsAndSides(nextOffset):
    scanner = GameScanner({'profile': 'wars'})
    timestamp = 0
    for reverse, offset in [(False, (40, 80)), (True, nextOffset)]:
        for _ in range(3):
            scanner.feed(scene(), timestamp); timestamp += .4
        board = shogi.Board()
        for usi in [None, '7g7f', '3c3d', '2g2f', '8c8d']:
            if usi:
                board.push_usi(usi)
            for _ in range(4):
                scanner.feed(scene(board, reverse, offset), timestamp); timestamp += .1
    games = scanner.snapshot()['games']
    assert len(games) == 2
    assert [game['orientation'] for game in games] == ['sente', 'gote']
    assert games[0]['endTime'] == games[1]['boundaryTime']
    assert games[0]['board'] != games[1]['board']


def testStaticInitialPositionDoesNotCreateAGame():
    scanner = GameScanner({'profile': 'wars'})
    for time in range(4):
        scanner.feed(scene(shogi.Board()), time)
    assert scanner.snapshot()['games'] == []


def testMissingFirstMoveOrderDoesNotGuessSide():
    scanner = GameScanner({'profile': 'wars'})
    board = shogi.Board()
    scanner.feed(scene(board), 0)
    board.push_usi('7g7f'); board.push_usi('3c3d')
    for time in [.1, .2, 2.1, 2.2]:
        scanner.feed(scene(board), time)
    assert len(scanner.games) == 1
    assert scanner.games[0]['orientation'] is None


def testBriefHiddenPieceDoesNotFixTheWrongFirstMover():
    scanner = GameScanner({'profile': 'wars'})
    board = shogi.Board()
    scanner.feed(scene(board), 0)
    flicker = shogi.Board(); flicker.remove_piece_at(shogi.SQUARE_NAMES.index('3c'))
    scanner.feed(scene(flicker), .1)
    board.push_usi('7g7f')
    for time in [.2, .3, .4, .5]:
        scanner.feed(scene(board), time)
    assert len(scanner.games) == 1
    assert scanner.games[0]['orientation'] == 'sente'
