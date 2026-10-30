"""静的配信だけで動画を認識し、結果・保存・通信先を検証する。"""
import argparse
import json
from pathlib import Path
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from playwright.sync_api import sync_playwright
from backend.records import decodeRecord, parseRecord

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8001/dist-pwa/')
parser.add_argument('--video', default='20261029_135654000_iOS.MP4')
parser.add_argument('--reference', default='棋譜_20261029.kif')
parser.add_argument('--end', type=float, default=35)
parser.add_argument('--timeout', type=float, default=600)
parser.add_argument('--full', action='store_true')
parser.add_argument('--resume', action='store_true')
parser.add_argument('--serve', action='store_true')
parser.add_argument('--port', type=int, default=8002)
parser.add_argument('--offline', action='store_true')
args = parser.parse_args()
rootDir = Path(__file__).resolve().parents[1]
outputDir = rootDir / '.local' / 'pwa-validation'
outputDir.mkdir(parents=True, exist_ok=True)
server = None
if args.serve:
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *values):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(rootDir)))
    Thread(target=server.serve_forever, daemon=True).start()
    args.url = f'http://127.0.0.1:{args.port}/dist-pwa/'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1)
    page = context.new_page()
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: print('console:', message.text, flush=True) if message.type == 'error' else None)
    context.on('request', lambda request: requests.append(request.url))
    try:
        page.goto(args.url)
        page.wait_for_load_state('networkidle')
        if args.offline:
            page.locator('#offlineButton').click()
            page.wait_for_function("document.getElementById('notice').textContent.includes('オフライン用の保存が完了')", timeout=180000)
            context.set_offline(True)
            page.reload(); page.wait_for_load_state('networkidle')
        page.locator('#videoFile').set_input_files(str(rootDir / args.video))
        page.locator('#analyzeButton').wait_for(state='visible')
        page.wait_for_function("!document.getElementById('analyzeButton').disabled", timeout=60000)
        page.locator('details').filter(has=page.locator('#endTime')).locator('summary').click()
        page.locator('#endTime').fill('' if args.full else str(args.end))
        page.locator('#analyzeButton').click()
        deadline = time.monotonic() + args.timeout
        stopped = False; tick = 0
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            info = page.evaluate("({notice:document.getElementById('notice').textContent, progress:document.getElementById('progressDetail').textContent, busy:document.getElementById('analyzeButton').disabled, count:document.getElementById('moveCount').textContent})")
            tick += 1
            if tick % 5 == 0 or not info['busy']:
                print(json.dumps(info, ensure_ascii=True), flush=True)
            if args.resume and not stopped and page.locator('#analysisProgress').evaluate('(el) => el.value') > .03:
                page.locator('#cancelButton').click()
                page.wait_for_function("!document.getElementById('analyzeButton').disabled", timeout=60000)
                page.reload(); page.wait_for_load_state('networkidle')
                page.wait_for_function("!document.getElementById('resultBody').hidden", timeout=30000)
                page.locator('#videoFile').set_input_files(str(rootDir / args.video))
                page.wait_for_function("!document.getElementById('analyzeButton').disabled", timeout=60000)
                page.locator('#savedResult').click()
                page.locator('#resumeButton').click()
                stopped = True
                continue
            if not info['busy']:
                break
        else:
            raise AssertionError('解析の時間制限を超えました。')
        assert page.locator('#notice').get_attribute('class') == 'notice', page.locator('#notice').inner_text()
        assert not page.locator('#resultBody').is_hidden(), page.locator('#notice').inner_text()
        with page.expect_download() as download:
            page.locator('#downloadJson').click()
        resultPath = outputDir / (Path(args.video).stem + ('-full' if args.full else '-short') + '.json')
        download.value.save_as(resultPath)
        result = json.loads(resultPath.read_text(encoding='utf-8'))
        predicted = [row['usi'] for row in result['moves']]
        reference = parseRecord(decodeRecord((rootDir / args.reference).read_bytes()))['moves']
        print(json.dumps({'moves': predicted, 'elapsedSeconds': result['elapsedSeconds'], 'analyzedFrames': result['analyzedFrames'], 'matches': predicted == reference[:len(predicted)], 'errors': errors}, ensure_ascii=True), flush=True)
        assert predicted, '指し手がありません。'
        assert predicted == reference[:len(predicted)], '指し手が正解棋譜の冒頭と一致しません。'
        if args.full:
            assert predicted == reference, '全手が一致しません。'
        assert not errors, errors
        external = [url for url in requests if not url.startswith(('http://127.0.0.1:', 'blob:', 'data:'))]
        assert not external, external
        assert not any('/api/' in url for url in requests), 'APIサーバーへアクセスしています。'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '横方向にはみ出しています。'
        page.screenshot(path=str(outputDir / 'pwa-mobile.png'), full_page=True)
        with page.expect_download() as download:
            page.locator('#downloadKif').click()
        kifPath = outputDir / 'recognized.kif'; download.value.save_as(kifPath)
        assert parseRecord(decodeRecord(kifPath.read_bytes()))['moves'] == predicted
        if args.offline:
            # オフラインのまま合法性検証・比較・結果復元まで使えることを確かめる。
            page.locator('.edit-details summary').click()
            page.locator('#editMoves').fill('7g7e')
            page.locator('#saveEdits').click()
            page.wait_for_function("document.getElementById('notice').classList.contains('error')")
            page.locator('#editMoves').fill('\n'.join(predicted))
            page.locator('#termination').select_option('投了')
            page.locator('#saveEdits').click()
            page.wait_for_function("document.getElementById('notice').textContent.includes('全手の合法性を確認')", timeout=30000)
            page.locator('.compare-details summary').click()
            page.locator('#referenceFile').set_input_files(str(rootDir / args.reference))
            page.wait_for_function("document.getElementById('comparison').textContent.length > 0", timeout=30000)
            assert f'冒頭から{len(predicted)}手連続一致' in page.locator('#comparison').inner_text()
            page.reload(); page.wait_for_load_state('networkidle')
            page.wait_for_function("!document.getElementById('resultBody').hidden", timeout=30000)
            assert page.locator('#moveCount').inner_text() == f'{len(predicted)} 手'
            assert page.locator('#termination').input_value() == '投了'
            assert page.locator('#analyzeButton').is_disabled()
            print('PASS: offline cold load, recognition, editing, comparison, reload without original video', flush=True)
        print('PASS: static browser recognition, local files, KIF/JSON export, mobile layout', flush=True)
    except Exception:
        page.screenshot(path=str(outputDir / 'pwa-failure.png'), full_page=True)
        print('PAGE ERRORS:', errors, flush=True)
        print('NOTICE:', page.locator('#notice').all_text_contents(), flush=True)
        raise
    finally:
        browser.close()
        if server:
            server.shutdown()
            server.server_close()
