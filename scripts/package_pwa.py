"""検証済みの静的配布物だけをZIPにまとめる。"""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

rootDir = Path(__file__).resolve().parents[1]
siteDir = rootDir / 'dist-pwa'
names = json.loads((siteDir / 'asset-manifest.json').read_text(encoding='utf-8')) + ['asset-manifest.json', 'sw.js']
outputDir = rootDir / 'outputs'
outputDir.mkdir(exist_ok=True)
target = outputDir / 'kifu-okoshi-pwa-pages.zip'
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for name in sorted(set(names)):
        path = (siteDir / name).resolve()
        if not path.is_relative_to(siteDir.resolve()) or path.suffix.lower() in ('.mp4', '.mov', '.kif', '.npz'):
            raise ValueError(f'配布対象外のファイルです：{name}')
        archive.write(path, name)
with ZipFile(target) as archive:
    if archive.testzip() is not None:
        raise RuntimeError('ZIPの整合性を確認できません。')
print(f'{target.name}: {target.stat().st_size / 1024 / 1024:.1f} MiB / {len(set(names))} files')
