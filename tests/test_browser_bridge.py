import json
import cv2
import numpy as np
import pytest
import shogi
from backend.recognition import FrameRecognizer
from backend.browser_bridge import bridgeCall, validateSettings
from tests.test_recognition import renderBoard


def settings():
    return {'startTime': 0, 'endTime': 10, 'sampleFps': 30, 'profile': 'wars',
            'orientation': 'sente', 'board': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}


def testResumePreservesPendingAndEveryMove():
    board = shogi.Board()
    baseline = FrameRecognizer(settings(), renderBoard(board))
    restored = FrameRecognizer(settings(), renderBoard(board))
    expected = ['7g7f', '3c3d', '2g2f', '8c8d']
    time = 0
    for usi in expected:
        board.push_usi(usi)
        frame = renderBoard(board)
        for _ in range(3):
            time += .1
            baseline.feed(frame, time)
            restored.feed(frame, time)
            restored = FrameRecognizer.restore(json.loads(json.dumps(restored.checkpoint())))
    assert [row['usi'] for row in restored.result()['moves']] == expected
    assert restored.result() == baseline.result()


def testDuplicateFrameCannotSatisfyConfirmation():
    board = shogi.Board()
    core = FrameRecognizer(settings(), renderBoard(board))
    board.push_usi('7g7f')
    frame = renderBoard(board)
    core.feed(frame, .1)
    for _ in range(10):
        core.feed(frame, .1)
    assert core.result()['moves'] == []
    assert core.analyzedFrames == 1
    core.feed(frame, .2)
    assert core.result()['moves'][0]['usi'] == '7g7f'


def testRgbaBridgeAndEditedTimes():
    board = shogi.Board()
    rgba = cv2.cvtColor(renderBoard(board), cv2.COLOR_BGR2RGBA)
    bridgeCall('start', json.dumps({'settings': settings(), 'width': 396, 'height': 438}), rgba.ravel())
    board.push_usi('7g7f')
    rgba = cv2.cvtColor(renderBoard(board), cv2.COLOR_BGR2RGBA)
    for time in (.1, .2):
        bridgeCall('frame', json.dumps({'time': time, 'width': 396, 'height': 438}), rgba.ravel())
    result = json.loads(bridgeCall('snapshot', '{}'))['result']
    assert result['moves'][0]['usi'] == '7g7f'
    edited = json.loads(bridgeCall('edit', json.dumps({'result': result, 'moves': ['2g2f'], 'termination': None})))
    assert edited['moves'][0]['time'] is None
    assert edited['moves'][0]['edited']
    with pytest.raises(ValueError):
        bridgeCall('edit', json.dumps({'result': result, 'moves': ['7g7e']}))


@pytest.mark.parametrize('key,value', [('sampleFps', 0), ('startTime', float('nan')), ('endTime', -1), ('orientation', 'invalid')])
def testBrowserSettingsAreValidated(key, value):
    invalid = settings(); invalid[key] = value
    with pytest.raises(ValueError):
        validateSettings(invalid)
