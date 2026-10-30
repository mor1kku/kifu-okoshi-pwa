"""短時間の局面が連続する合成動画を作る。元動画やユーザーの棋譜は使わない。"""
import json
from pathlib import Path
import cv2
import shogi
from backend.records import exportRecord
from tests.test_recognition import renderBoard

rootDir = Path(__file__).resolve().parents[1]
outputDir = rootDir / '.local' / 'fast-validation'
outputDir.mkdir(parents=True, exist_ok=True)

def makeFixture(name, moves, holdFrames):
    writer = cv2.VideoWriter(str(outputDir / f'{name}.webm'), cv2.VideoWriter_fourcc(*'VP90'), 60, (396, 438))
    if not writer.isOpened():
        raise RuntimeError('VP9エンコーダーを利用できません。')
    board = shogi.Board()
    try:
        for index, usi in enumerate([None] + moves):
            if usi:
                board.push_usi(usi)
            frame = renderBoard(board)
            for _ in range(holdFrames[index]):
                writer.write(frame)
    finally:
        writer.release()
    (outputDir / f'{name}.kif').write_text(exportRecord(moves), encoding='utf-8')
    (outputDir / f'{name}.json').write_text(json.dumps({'version': 1, 'startTime': 0, 'profile': 'wars',
        'orientation': 'sente', 'sampleFps': 12, 'board': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}), encoding='utf-8')
    print(f'{name}: 60 fps, {len(moves)} moves, frames per position: {holdFrames}')


makeFixture('fast-wars', ['7g7f', '3c3d', '2g2f', '8c8d', '2f2e', '8d8e'], [60, 2, 2, 2, 2, 2, 60])
makeFixture('fast-drop', ['7g7f', '3c3d', '8h2b', '8b2b', 'B*5e', '4c4d', '5e4f'], [60, 12, 12, 12, 12, 1, 1, 60])
