"""APIサーバーを含まないGitHub Pages用の配布物を作る。"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import struct
import uuid
import zipfile
import zlib
import shogi

rootDir = Path(__file__).resolve().parents[1]
buildCacheDir = rootDir / '.local' / 'pwa-builds'
if not buildCacheDir.resolve().is_relative_to(rootDir):
    raise RuntimeError('ビルド先がプロジェクトの外にあります。')
outputDir = buildCacheDir / ('build-' + uuid.uuid4().hex)
outputDir.mkdir(parents=True)
runtimeDir = rootDir / 'node_modules' / 'pyodide'
vendorDir = rootDir / '.local' / 'pwa-vendor'
packageNames = json.loads((vendorDir / 'packages.json').read_text(encoding='utf-8'))
runtimeFiles = ['pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json']


def copy(source, destination):
    target = outputDir / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


for path in (rootDir / 'pwa').glob('*.js'):
    copy(path, path.name)
for name in ('app.js', 'style.css'):
    copy(rootDir / 'frontend' / name, name)
for name in runtimeFiles:
    copy(runtimeDir / name, 'vendor/pyodide/' + name)
lock = json.loads((runtimeDir / 'pyodide-lock.json').read_text(encoding='utf-8'))
hashes = {item['file_name']: item['sha256'] for item in lock['packages'].values()}
for name in packageNames:
    source = vendorDir / name
    if hashlib.sha256(source.read_bytes()).hexdigest() != hashes[name]:
        raise RuntimeError(f'{name} のハッシュが一致しません。prepare:pwaを再実行してください。')
    copy(source, 'vendor/pyodide/' + name)
copy(rootDir / 'node_modules/mediabunny/dist/bundles/mediabunny.mjs', 'vendor/mediabunny.mjs')
copy(rootDir / 'node_modules/mediabunny/LICENSE', 'licenses/mediabunny.txt')
mediabunnyDir = rootDir / 'node_modules' / 'mediabunny'
with zipfile.ZipFile(outputDir / 'mediabunny-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((mediabunnyDir / 'src').rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(mediabunnyDir).as_posix())
    for name in ('package.json', 'README.md', 'LICENSE'):
        archive.write(mediabunnyDir / name, name)
copy(rootDir / 'LICENSE', 'LICENSE')
for path in (rootDir / 'licenses').glob('*.txt'):
    copy(path, 'licenses/' + path.name)
shogiDistribution = importlib.metadata.distribution('python-shogi')
if shogiDistribution.version != '1.1.1':
    raise RuntimeError('python-shogi 1.1.1をインストールしてください。')
copy(shogiDistribution.locate_file('python_shogi-1.1.1.dist-info/LICENSE'), 'licenses/python-shogi.txt')
shogiDir = Path(shogi.__file__).parent
with zipfile.ZipFile(outputDir / 'python.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in ('__init__.py', 'recognition.py', 'records.py', 'browser_bridge.py'):
        archive.write(rootDir / 'backend' / name, 'backend/' + name)
    for path in shogiDir.rglob('*.py'):
        archive.write(path, 'shogi/' + path.relative_to(shogiDir).as_posix())
    archive.write(outputDir / 'licenses/python-shogi.txt', 'shogi/LICENSE')
html = (rootDir / 'frontend/index.html').read_text(encoding='utf-8')
html = html.replace('href="/style.css"', 'href="./style.css"').replace('href="/"', 'href="./"').replace('src="/app.js"', 'src="./bootstrap.js"')
html = html.replace('</head>', '<link rel="manifest" href="./manifest.webmanifest">\n<link rel="icon" href="./icons/icon-192.png">\n<link rel="apple-touch-icon" href="./icons/icon-192.png">\n<meta name="apple-mobile-web-app-capable" content="yes">\n</head>')
html = html.replace('試作版 / 平手対応', '端末内解析')
html = html.replace('動画から棋譜へ <span class="version">', 'v1.2.2 <span class="version">')
html = html.replace('ローカル起動中はこのPCで処理します。<br>クラウド利用時は接続先サーバーで処理します。',
                    '動画はこの端末内で処理し、送信しません。<br>画面を閉じると一時停止し、戻ると自動で再開します。<br>アプリが終了した場合は、同じ動画を選んで途中から再開できます。<br>初回は解析エンジンをダウンロードします。')
html = html.replace('<footer>', '<section class="step"><h2>iPhoneで使う</h2><p>Safariの共有メニューから「ホーム画面に追加」。初回は通信できる場所で準備してください。</p><button id="offlineButton" class="secondary">オフライン用にアプリを保存</button><p class="field-help">動画の原本は「写真」または「ファイル」に保存してください。途中から再開する場合は同じ動画を選び直します。端末の保存領域が消去される場合に備え、棋譜はKIFでも保存してください。</p></section><footer>')
(outputDir / 'index.html').write_text(html, encoding='utf-8')
profiles = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((rootDir / 'profiles').glob('*.json'))]
(outputDir / 'profiles.json').write_text(json.dumps(profiles, ensure_ascii=False), encoding='utf-8')
manifest = {'id': './', 'name': '棋譜おこし', 'short_name': '棋譜おこし', 'lang': 'ja', 'start_url': './', 'scope': './',
            'display': 'standalone', 'background_color': '#f5f3ed', 'theme_color': '#223c34',
            'icons': [{'src': f'icons/icon-{size}.png', 'sizes': f'{size}x{size}', 'type': 'image/png', 'purpose': 'any'} for size in (192, 512)]}
(outputDir / 'manifest.webmanifest').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


def icon(size):
    # 盤と駒の幾何学図形で、外部フォントに依存しないホーム画面アイコンを作る。
    data = bytearray()
    for y in range(size):
        data.append(0)
        for x in range(size):
            nx, ny = x / size, y / size
            color = (34, 60, 52)
            if .19 < nx < .81 and .19 < ny < .81:
                color = (211, 178, 120)
                if any(abs(nx - value) < .004 or abs(ny - value) < .004 for value in (.19, .345, .5, .655, .81)):
                    color = (75, 71, 48)
                if .39 < ny < .65 and abs(nx - .5) < .10 + (ny - .39) * .14:
                    color = (248, 236, 201)
            data.extend(color)
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b'')


(outputDir / 'icons').mkdir(exist_ok=True)
for size in (192, 512):
    (outputDir / f'icons/icon-{size}.png').write_bytes(icon(size))
(outputDir / '.nojekyll').write_text('', encoding='utf-8')
(outputDir / 'THIRD-PARTY.txt').write_text(
    'python-shogi 1.1.1: GPL-3.0-only; source and license are included in python.zip.\n'
    'This application: GPL-3.0-only; see LICENSE and corresponding-source.zip.\n'
    'Mediabunny 1.64.0: MPL-2.0; licenses/mediabunny.txt; unmodified source: mediabunny-source.zip\n'
    'Pyodide 314.0.7: MPL-2.0; licenses/pyodide-314.0.7.txt; source: https://github.com/pyodide/pyodide/tree/314.0.7\n'
    'CPython 3.14.2: licenses/cpython-3.14.2.txt; source: https://github.com/python/cpython/tree/v3.14.2\n'
    'NumPy and OpenCV: licenses are included in the corresponding wheel files; '
    'build recipes: https://github.com/pyodide/pyodide-recipes\n', encoding='utf-8')
sourceFiles = [rootDir / name for name in ('LICENSE', '.gitignore', '.dockerignore', 'Dockerfile', 'compose.yaml', 'start.ps1', 'package.json', 'package-lock.json', 'requirements.txt', 'requirements-dev.txt', 'pytest.ini', '.github/workflows/pages.yml')]
for directory, pattern in (('backend', '*.py'), ('frontend', '*'), ('pwa', '*.js'), ('profiles', '*.json'), ('scripts', '*.py'), ('scripts', '*.mjs'), ('tests', '*.py'), ('tests', '*.cjs'), ('licenses', '*.txt')):
    sourceFiles.extend(path for path in (rootDir / directory).glob(pattern) if path.is_file())
with zipfile.ZipFile(outputDir / 'corresponding-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    readme = rootDir / 'README-PWA.md'
    archive.write(readme if readme.exists() else rootDir / 'README.md', 'README.md')
    for path in sorted(set(sourceFiles)):
        archive.write(path, path.relative_to(rootDir).as_posix())
    archive.write(outputDir / 'licenses/python-shogi.txt', 'licenses/python-shogi.txt')
files = sorted(path.relative_to(outputDir).as_posix() for path in outputDir.rglob('*') if path.is_file() and path.name not in ('sw.js', 'asset-manifest.json', '.nojekyll'))
digest = hashlib.sha256()
for name in files:
    digest.update(name.encode()); digest.update((outputDir / name).read_bytes())
sw = (rootDir / 'pwa/sw.js').read_text(encoding='utf-8').replace('__BUILD_ID__', digest.hexdigest()[:16])
(outputDir / 'sw.js').write_text(sw, encoding='utf-8')
(outputDir / 'asset-manifest.json').write_text(json.dumps(files), encoding='utf-8')
total = sum(path.stat().st_size for path in outputDir.rglob('*') if path.is_file())
# 旧成果物は退避し、今回生成したファイルだけを公開ディレクトリに置く。
siteDir = rootDir / 'dist-pwa'
if siteDir.is_symlink() or siteDir.resolve() != rootDir / 'dist-pwa':
    raise RuntimeError('公開先がプロジェクトの外にあります。')
if siteDir.exists():
    siteDir.rename(buildCacheDir / ('previous-' + uuid.uuid4().hex))
outputDir.rename(siteDir)
print(f'dist-pwa: {len(files) + 3} files, {total / 1024 / 1024:.1f} MiB. 動画・正解棋譜・APIサーバーは含みません。')
