"""画面共有の診断UIとSWを検証する。OSの非表示動作は実機で別途確認する。"""
import argparse
import json
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

fakeCapture = r"""
window.captureCalls = 0; window.captureTracks = []; window.captureMode = 'allow';
function createCapture() {
  const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 240;
  const context = canvas.getContext('2d'); let tick = 0;
  const paint = () => { context.fillStyle = `rgb(${++tick % 255}, ${tick * 7 % 255}, 80)`; context.fillRect(0, 0, 320, 240); };
  paint(); const stream = canvas.captureStream(20), timer = setInterval(paint, 80);
  window.captureTracks.push(...stream.getTracks());
  for (const track of stream.getTracks()) { const stop = track.stop.bind(track); track.stop = () => { clearInterval(timer); stop(); }; }
  return stream;
}
Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
  getDisplayMedia: async options => {
    window.captureCalls++; window.captureOptions = options;
    if (window.captureMode === 'deny') throw new DOMException('cancelled', 'NotAllowedError');
    if (window.captureMode === 'pending') return new Promise(resolve => { window.resolveCapture = () => resolve(createCapture()); });
    return createCapture();
  }
} });
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.copiedReport = text; } } });
"""


@contextmanager
def servePage(enabled):
    if not enabled:
        yield None
        return
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *values):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(Path('dist-pwa').resolve())))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8016/')
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    outputDir = Path('.local/screen-share-validation')
    outputDir.mkdir(parents=True, exist_ok=True)
    with servePage(args.serve) as servedUrl, sync_playwright() as playwright:
        args.url = servedUrl or args.url
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1)
        context.add_init_script(fakeCapture)
        page = context.new_page()
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append((request.method, request.url)))
        page.goto(args.url)
        page.wait_for_load_state('networkidle')
        page.evaluate('async () => { await navigator.serviceWorker.ready; }')
        page.reload()
        page.wait_for_load_state('networkidle')
        assert page.evaluate('Boolean(navigator.serviceWorker.controller)')
        page.get_by_role('link', name='別アプリの画面共有を診断する').click()
        page.wait_for_url('**/screen-share-test.html')
        page.wait_for_load_state('networkidle')
        assert '別アプリの動画を' in page.locator('h1').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(outputDir / 'mobile.png'), full_page=True)
        page.get_by_role('button', name='画面共有を試す').click()
        page.wait_for_function('() => Number(document.getElementById("sampleCount").textContent) >= 4')
        assert page.evaluate('captureOptions.audio === false')
        page.evaluate("""() => {
          window.testVisibility = 'hidden';
          Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => testVisibility });
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.wait_for_timeout(12000)
        page.evaluate("testVisibility = 'visible'; document.dispatchEvent(new Event('visibilitychange'))")
        page.get_by_role('button', name='診断を終了').click()
        assert '映像の変化を読み取れました' in page.locator('#resultText').inner_text()
        assert page.evaluate('captureTracks.every(track => track.readyState === "ended")')
        page.get_by_role('button', name='結果をコピー').click()
        page.wait_for_function('() => Boolean(window.copiedReport)')
        assert '棋譜解析はまだ未検証' in page.evaluate('copiedReport')
        with page.expect_download() as downloadInfo:
            page.get_by_role('button', name='JSONを保存').click()
        downloadInfo.value.save_as(str(outputDir / 'report.json'))
        report = json.loads((outputDir / 'report.json').read_text(encoding='utf-8'))
        assert report['status'] == 'complete' and report['intervals'][1]['visibility'] == 'hidden'
        page.reload()
        page.wait_for_load_state('networkidle')
        assert page.evaluate('captureCalls') == 0
        assert '映像の変化を読み取れました' in page.locator('#resultText').inner_text()
        page.evaluate("captureMode = 'deny'")
        page.get_by_role('button', name='画面共有を試す').click()
        page.wait_for_function('() => document.getElementById("resultText").textContent.includes("NotAllowedError")')
        page.evaluate("captureMode = 'pending'")
        page.get_by_role('button', name='画面共有を試す').click()
        page.wait_for_function('() => typeof resolveCapture === "function"')
        page.get_by_role('button', name='診断を終了').click()
        page.evaluate('resolveCapture()')
        page.wait_for_function('() => captureTracks.length > 0 && captureTracks.every(track => track.readyState === "ended")')
        page.evaluate("captureMode = 'allow'")
        page.get_by_role('button', name='画面共有を試す').click()
        page.wait_for_function('() => Number(document.getElementById("sampleCount").textContent) >= 2')
        page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide'))")
        assert page.evaluate('captureTracks.every(track => track.readyState === "ended")')
        assert 'interrupted' in page.locator('#resultText').inner_text()
        page.goto(args.url + 'screen-share-test.html?retry=1')
        page.wait_for_load_state('networkidle')
        assert '別アプリの動画を' in page.locator('h1').inner_text()
        context.set_offline(True)
        page.reload()
        page.wait_for_load_state('networkidle')
        assert '別アプリの動画を' in page.locator('h1').inner_text()
        page.add_init_script("Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined })")
        page.reload()
        page.wait_for_load_state('networkidle')
        assert page.get_by_role('button', name='画面共有を試す').is_disabled()
        assert 'unsupported' in page.locator('#resultText').inner_text()
        assert not errors, errors
        assert all(method == 'GET' and urlparse(url).netloc == urlparse(args.url).netloc for method, url in requests), requests
        browser.close()
    print('PASS: SW navigation/offline, simulated background frames, copy/export, denial, late permission cleanup, pagehide, no automatic recapture, no uploads. iPhone background capture is unverified.')


if __name__ == '__main__':
    main()
