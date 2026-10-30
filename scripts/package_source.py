"""動画・棋譜・一時データを含めず、クラウド移行用のソースをまとめる。"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

rootDir = Path(__file__).resolve().parents[1]
outputDir = rootDir / 'outputs'
outputDir.mkdir(exist_ok=True)
sourceFiles = ['README.md', 'requirements.txt', 'requirements-dev.txt', 'Dockerfile', 'compose.yaml',
               'start.ps1', 'package.json', 'package-lock.json', 'LICENSE', 'pytest.ini', '.gitignore', '.dockerignore', '.github/workflows/pages.yml']
for folder in ('backend', 'frontend', 'tests', 'scripts', 'profiles', 'pwa', 'licenses'):
    sourceFiles.extend(str(path.relative_to(rootDir)) for path in (rootDir / folder).rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts and 'fixtures' not in path.parts and path.suffix != '.pyc')
archivePath = outputDir / 'kifu-okoshi-cloud.zip'
with ZipFile(archivePath, 'w', ZIP_DEFLATED) as archive:
    for name in sorted(sourceFiles):
        archive.write(rootDir / name, 'kifu-okoshi/' + Path(name).as_posix())
with ZipFile(archivePath, 'r') as archive:
    if archive.testzip() is not None:
        raise RuntimeError('ZIPファイルの整合性確認に失敗しました。')
print(f'{archivePath.name}: {len(sourceFiles)} files, {archivePath.stat().st_size} bytes')
