"""旧版を保存したブラウザーで、データを残した更新と複数タブの保護を検証する。"""
import argparse
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


parser = argparse.ArgumentParser()
parser.add_argument('--old', required=True, type=Path, help='旧版の静的配信フォルダー')
parser.add_argument('--new', default='dist-pwa', type=Path)
args = parser.parse_args()
state = {'directory': args.old.resolve(), 'next': False}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *values):
        pass

    def translate_path(self, path):
        relative = urlparse(path).path.removeprefix('/kifu/').lstrip('/') or 'index.html'
        target = (state['directory'] / relative).resolve()
        return str(target if target.is_relative_to(state['directory']) else state['directory'] / 'missing')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health.html':
            body = b'<!doctype html><title>update observer</title>'
            kind = 'text/html'
        elif state['next'] and path in ('/kifu/', '/kifu/index.html', '/kifu/sw.js'):
            name = 'sw.js' if path.endswith('sw.js') else 'index.html'
            text = (state['directory'] / name).read_text(encoding='utf-8')
            if name == 'sw.js':
                text = re.sub(r'(const cacheName = `\$\{cachePrefix\})([^`]+)', r'\g<1>\g<2>-update-test', text)
            else:
                text = text.replace('v1.2.3 <span', 'v1.2.4-test <span')
            body = text.encode('utf-8'); kind = 'text/javascript' if name == 'sw.js' else 'text/html'
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Thread(target=server.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{server.server_port}'
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    errors = []
    try:
        old = context.new_page(); old.on('pageerror', lambda error: errors.append(str(error)))
        old.goto(origin + '/kifu/'); old.wait_for_load_state('networkidle')
        old.wait_for_function('navigator.serviceWorker.controller !== null', timeout=120000)
        assert 'v1.2.1' in old.locator('.masthead-note').inner_text()
        awaitSeed = """async () => {
            const storage = await import('./storage.js');
            const video = { id: 'test-video', name: 'local-test.mp4', width: 100, height: 100, duration: 60, fps: 30, size: 1000 };
            const settings = { videoId: video.id, startTime: 0, endTime: null, sampleFps: 12, profile: 'wars', orientation: 'sente', board: { x: 0, y: 0, width: 1, height: 1 } };
            const result = { id: 'test-job', videoId: video.id, moves: [], settings, warnings: [], kif: 'test', engineVersion: '1.2.1' };
            await storage.stored('job:test-job', { id: 'test-job', videoId: video.id, video, settings, result, status: 'complete' });
            await storage.stored('video:' + video.id, { ...video, latestJob: 'test-job' });
            localStorage.setItem(kifuLocal.storageKey('kifu-last-job'), 'test-job');
        }"""
        old.evaluate(awaitSeed); old.reload()
        old.wait_for_function("!document.getElementById('resultBody').hidden")
        oldCaches = old.evaluate('caches.keys()')
        state['directory'] = args.new.resolve()
        old.evaluate('async () => { window.testRegistration = await navigator.serviceWorker.getRegistration(); await window.testRegistration.update(); }')
        old.wait_for_function('Boolean(window.testRegistration.waiting)', timeout=120000)
        old.reload(); old.wait_for_load_state('networkidle')
        assert 'v1.2.1' in old.locator('.masthead-note').inner_text(), '旧タブを残した通常再読込の再現に失敗'
        observer = context.new_page(); observer.goto(origin + '/health.html')
        observer.evaluate("async () => { window.testRegistration = await navigator.serviceWorker.getRegistration('/kifu/'); }")
        old.close()
        observer.wait_for_function("!testRegistration.waiting && !testRegistration.installing && testRegistration.active?.state === 'activated'", timeout=30000)
        assert not set(oldCaches).intersection(observer.evaluate('caches.keys()'))
        observer.goto(origin + '/kifu/'); observer.wait_for_load_state('networkidle')
        header = observer.locator('.masthead-note').inner_text()
        if 'v1.2.3' not in header:
            print('UPDATE DEBUG:', header, observer.evaluate("""async () => {
                const r = await navigator.serviceWorker.getRegistration();
                const names = await caches.keys(); const cached = [];
                for (const name of names) { const c = await caches.open(name); const response = await c.match(new URL('./index.html', location.href)); cached.push([name, response ? (await response.text()).match(/v1\\.2\\.\\d/)?.[0] : null]); }
                return { active: r?.active?.state, waiting: r?.waiting?.state, installing: r?.installing?.state, cached };
            }"""), flush=True)
        assert 'v1.2.3' in header
        observer.wait_for_function("!document.getElementById('resultBody').hidden")
        assert '解析v1.2.1' in observer.locator('#resultSummary').inner_text()
        print('PASS: closing all old app tabs activates the update and keeps the old result', flush=True)
        second = context.new_page(); second.goto(origin + '/kifu/'); second.wait_for_load_state('networkidle')
        state['next'] = True
        observer.locator('#updateButton').click()
        observer.wait_for_function("document.getElementById('notice').textContent.includes('ほかの')", timeout=120000)
        assert 'v1.2.3' in observer.locator('.masthead-note').inner_text()
        assert not observer.locator('#updateButton').is_disabled()
        second.close()
        observer.locator('#updateButton').click()
        observer.wait_for_function("document.querySelector('.masthead-note').textContent.includes('v1.2.4-test')", timeout=30000)
        observer.wait_for_function("!document.getElementById('resultBody').hidden")
        assert '解析v1.2.1' in observer.locator('#resultSummary').inner_text()
        context.set_offline(True); observer.reload()
        observer.wait_for_function("!document.getElementById('resultBody').hidden")
        assert 'v1.2.4-test' in observer.locator('.masthead-note').inner_text()
        assert not errors, errors
        print('PASS: update button protects other tabs, preserves saved results, and works offline after updating', flush=True)
    finally:
        browser.close(); server.shutdown(); server.server_close()
