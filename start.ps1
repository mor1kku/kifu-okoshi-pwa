$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python仮想環境の作成に失敗しました。' }
}
$dependencyCheck = @'
import importlib.util, sys
sys.exit(0 if all(importlib.util.find_spec(name) for name in ('cv2', 'fastapi', 'uvicorn', 'shogi', 'python_multipart')) else 1)
'@
$dependencyCheck | & $pythonPath -
if ($LASTEXITCODE -ne 0) {
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依存ライブラリのインストールに失敗しました。' }
}
$env:KIFU_SAMPLE_DIR = $PSScriptRoot
Write-Host '棋譜おこし: http://127.0.0.1:8000'
& $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
