from pathlib import Path
import json
import cv2

rootDir = Path(__file__).resolve().parents[1]
videoPath = next(rootDir.glob('*.MP4'))
capture = cv2.VideoCapture(str(videoPath))
fps = capture.get(cv2.CAP_PROP_FPS)
frameCount = capture.get(cv2.CAP_PROP_FRAME_COUNT)
print(json.dumps({'fps': fps, 'frames': frameCount, 'duration': frameCount / fps,
                  'width': capture.get(3), 'height': capture.get(4)}))
outputDir = rootDir / '.local' / 'inspection'
outputDir.mkdir(parents=True, exist_ok=True)
for seconds in [0, 1, 3, 5, 10, 20, 40, 80, 160, 240, 320]:
    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = capture.read()
    if ok:
        scale = min(1, 900 / frame.shape[0])
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
        ok, encoded = cv2.imencode('.jpg', frame)
        (outputDir / f'{seconds:03d}.jpg').write_bytes(encoded.tobytes())
capture.release()
