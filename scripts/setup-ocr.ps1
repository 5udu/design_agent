$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
python -c "import sys; assert sys.version_info[:2] == (3,12), 'Use Python 3.12 x64 for this Windows OCR lock'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required' }
$ocrPython = Join-Path $projectRoot '.ocr-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ocrPython)) {
    python -m venv .ocr-venv
    if ($LASTEXITCODE -ne 0) { throw 'OCR environment creation failed' }
}
& $ocrPython scripts/download_ocr_wheel.py
if ($LASTEXITCODE -ne 0) { throw 'Official wheel download or SHA256 verification failed' }
& $ocrPython -m pip install data/wheels/paddlepaddle-3.3.1-cp312-cp312-win_amd64.whl --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw 'Paddle installation failed' }
& $ocrPython -m pip install -r backend/requirements-ocr.lock --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw 'OCR dependencies installation failed' }
& $ocrPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'OCR dependency check failed' }
Write-Output 'OCR setup complete. Run scripts\start.ps1. First recognition downloads official PP-OCRv5 models.'
