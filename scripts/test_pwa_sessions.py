"""合成した連続対局を実際のWorkerで探索し、局別の修正・保存まで確認する。"""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import time
import cv2
import shogi
from playwright.sync_api import sync_playwright
from tests.test_auto_setup import scene
from backend.records import parseRecord

rootDir = Path(__file__).resolve().parents[1]
outputDir = rootDir / '.local' / 'session-validation'
outputDir.mkdir(parents=True, exist_ok=True)
moves = ['7g7f', '3c3d', '2g2f', '8c8d', '2f2e', '8d8e']
video = outputDir / 'two-games.webm'
writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'VP90'), 30, (600, 800))
if not writer.isOpened():
    raise RuntimeError('VP9エンコーダーを利用できません。')
try:
    for reverse, offset in [(False, (40, 80)), (True, (100, 220))]:
        for _ in range(24):
            writer.write(scene())
        board = shogi.Board()
        for usi in [None] + moves:
            if usi:
                board.push_usi(usi)
            for _ in range(30 if usi is None else 10):
                writer.write(scene(board, reverse, offset))
finally:
    writer.release()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 8013), partial(Handler, directory=str(rootDir)))
Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    page.add_init_script("document.addEventListener('kifu-session', e => { window.sessionState = e.detail; });")
    errors, uploads = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    context.on('request', lambda request: uploads.append(request.url) if request.method not in ('GET', 'HEAD') else None)

    def waitReady():
        page.wait_for_function("!document.getElementById('analyzeButton').disabled", timeout=60000)

    def waitState(expression):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if page.evaluate(expression):
                return
            page.wait_for_timeout(1500)
            print(json.dumps([page.locator('#progressMessage').inner_text(), page.locator('#progressDetail').inner_text()], ensure_ascii=True), flush=True)
        raise AssertionError(page.locator('#notice').inner_text())

    try:
        page.goto('http://127.0.0.1:8013/dist-pwa/')
        page.locator('#videoFile').set_input_files(str(video)); waitReady()
        page.locator('#profile').select_option('wars')
        page.locator('#findGames').click()
        waitState("window.sessionState?.games.length > 0 && !document.getElementById('findGames').disabled")
        session = page.evaluate('window.sessionState')
        assert len(session['games']) == 2, session
        assert [game['settings']['orientation'] for game in session['games']] == ['sente', 'gote'], session
        page.locator('#gameList button').nth(1).click()
        page.wait_for_function("document.getElementById('setupTitle').textContent.startsWith('第2局')")
        page.locator('#orientation').select_option('sente'); page.locator('#saveGame').click()
        page.locator('#gameList button').nth(0).click()
        assert page.locator('#orientation').input_value() == 'sente'
        page.locator('#gameList button').nth(1).click()
        assert page.locator('#orientation').input_value() == 'sente'
        page.locator('#orientation').select_option('gote'); page.locator('#saveGame').click()
        page.locator('#autoBoard').click(); waitReady()
        page.locator('#analyzeGames').click()
        waitState("window.sessionState?.queue.status === 'complete'")
        waitReady()
        session = page.evaluate('window.sessionState')
        assert [game['moveCount'] for game in session['games']] == [6, 6], session
        for index in range(2):
            page.locator('#gameList button').nth(index).click()
            page.wait_for_function("!document.getElementById('resultBody').hidden")
            with page.expect_download() as download:
                page.locator('#downloadKif').click()
            assert download.value.suggested_filename == f'game-{index+1:02}.kif'
            path = outputDir / download.value.suggested_filename
            download.value.save_as(path)
            assert parseRecord(path.read_text(encoding='utf-8-sig'))['moves'] == moves
        page.locator('details').filter(has=page.locator('#editMoves')).locator('summary').click()
        page.locator('#editMoves').fill('\n'.join(moves[:-1])); page.locator('#saveEdits').click(); waitReady()
        assert page.locator('#moveCount').inner_text() == '5 手'
        page.locator('#gameList button').nth(0).click()
        page.wait_for_function("document.getElementById('moveCount').textContent === '6 手'")
        page.reload(); page.locator('#videoFile').set_input_files(str(video)); waitReady()
        assert page.locator('#gameList button').count() == 2
        page.locator('#gameList button').nth(1).click()
        page.wait_for_function("document.getElementById('moveCount').textContent === '5 手'")
        page.screenshot(path=str(outputDir / 'sessions-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '横にはみ出しています'
        assert not errors, errors
        assert not uploads, uploads
        (outputDir / 'session.json').write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PASS: 2 games / orientation / per-game settings / batch / KIF / edit isolation / reload / no uploads', flush=True)
    finally:
        page.screenshot(path=str(outputDir / 'last-screen.png'), full_page=True)
        browser.close(); server.shutdown()
