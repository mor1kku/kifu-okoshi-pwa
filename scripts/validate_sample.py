"""実動画を独立認識し、その後に正解KIFと比較する。"""
from pathlib import Path
import argparse
import json
import sys

rootDir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(rootDir))
from backend.recognition import analyzeVideo
from backend.records import compareMoves, decodeRecord, exportRecord, parseRecord

parser = argparse.ArgumentParser()
parser.add_argument('--video', type=Path)
parser.add_argument('--reference', type=Path)
parser.add_argument('--settings', type=Path, default=rootDir / 'profiles' / 'kioh-sample.json')
parser.add_argument('--start', type=float)
parser.add_argument('--fps', type=float)
parser.add_argument('--output', type=Path, default=rootDir / '.local' / 'validation')
args = parser.parse_args()
videoPath = args.video or next(rootDir.glob('*.MP4'), None)
if videoPath is None:
    parser.error('--video で動画ファイルを指定してください。')
lastPercent = [-1]
def reportProgress(value):
    percent = int(value['progress'] * 100)
    if percent // 5 != lastPercent[0] // 5:
        print(f'{percent}% / {value["moveCount"]} moves / {value["time"]} seconds', flush=True)
        lastPercent[0] = percent
settings = json.loads(args.settings.read_text(encoding='utf-8'))
if args.start is not None:
    settings['startTime'] = args.start
if args.fps is not None:
    settings['sampleFps'] = args.fps
result = analyzeVideo(videoPath, settings, reportProgress)
result['settings'] = settings
args.output.mkdir(parents=True, exist_ok=True)
(args.output / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
(args.output / 'recognized.kif').write_text(exportRecord([row['usi'] for row in result['moves']]), encoding='utf-8-sig')
if args.reference:
    # 正解棋譜の読込は動画認識の完了後に限る。
    reference = parseRecord(decodeRecord(args.reference.read_bytes()))
    comparison = compareMoves([row['usi'] for row in result['moves']], reference['moves'])
    (args.output / 'comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(comparison), flush=True)
    sys.exit(0 if comparison['exactMatch'] else 1)
print(f'{len(result["moves"])} moves saved', flush=True)
