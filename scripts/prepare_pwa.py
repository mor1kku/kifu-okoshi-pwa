"""固定したPyodideの追加パッケージをハッシュ確認付きで取得する。"""
import hashlib
import json
from pathlib import Path
import urllib.request

rootDir = Path(__file__).resolve().parents[1]
runtimeDir = rootDir / 'node_modules' / 'pyodide'
lock = json.loads((runtimeDir / 'pyodide-lock.json').read_text(encoding='utf-8'))
version = json.loads((runtimeDir / 'package.json').read_text(encoding='utf-8'))['version']
vendorDir = rootDir / '.local' / 'pwa-vendor'
vendorDir.mkdir(parents=True, exist_ok=True)
packages = lock['packages']
selected = set()


def include(name):
    if name in selected:
        return
    selected.add(name)
    for dependency in packages[name]['depends']:
        include(dependency)


for name in ('numpy', 'opencv-python'):
    include(name)
for name in sorted(selected):
    package = packages[name]
    target = vendorDir / package['file_name']
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != package['sha256']:
        url = f'https://cdn.jsdelivr.net/pyodide/v{version}/full/{target.name}'
        print(f'Download: {name}', flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != package['sha256']:
            raise RuntimeError(f'{name} の整合性を確認できません。')
        target.write_bytes(content)
    print(f'{name}: {target.stat().st_size:,} bytes', flush=True)
(vendorDir / 'packages.json').write_text(json.dumps([packages[name]['file_name'] for name in sorted(selected)]), encoding='utf-8')
