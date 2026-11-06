$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run scripts\setup.ps1 first' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend first' }
$logsPath = Join-Path $projectRoot 'data\logs'
New-Item -ItemType Directory -Path $logsPath -Force | Out-Null
$ocrPython = Join-Path $projectRoot '.ocr-venv\Scripts\python.exe'
if (Test-Path -LiteralPath $ocrPython) {
    $ocrActive = $false
    try { $ocrActive = (Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 3).status -eq 'ok' } catch { }
    if (-not $ocrActive) {
        $ocrProcess = Start-Process -FilePath $ocrPython -ArgumentList @('scripts/ocr_service.py') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsPath 'ocr.out.log') -RedirectStandardError (Join-Path $logsPath 'ocr.err.log') -PassThru
        $ocrProcess.Id | Set-Content -LiteralPath (Join-Path $logsPath 'ocr.pid')
    }
} else { Write-Output 'OCR not installed: run scripts\setup-ocr.ps1 for local text recognition.' }
$apiActive = $false
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health/ready' -TimeoutSec 3
    $apiActive = $health.status -eq 'ok'
} catch { }
if (-not $apiActive) {
    $apiProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsPath 'api.out.log') -RedirectStandardError (Join-Path $logsPath 'api.err.log') -PassThru
    $apiProcess.Id | Set-Content -LiteralPath (Join-Path $logsPath 'api.pid')
}
& $pythonExe scripts/worker_status.py | Out-Null
if ($LASTEXITCODE -ne 0) {
    $workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','app.runtime.worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsPath 'worker.out.log') -RedirectStandardError (Join-Path $logsPath 'worker.err.log') -PassThru
    $workerProcess.Id | Set-Content -LiteralPath (Join-Path $logsPath 'worker.pid')
}
$apiReady = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try { $apiReady = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health/ready' -TimeoutSec 2).status -eq 'ok' } catch { }
    if ($apiReady) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $apiReady) { throw 'API did not become ready. See data\logs\api.err.log' }
Write-Output 'Studio: http://127.0.0.1:8000'
Write-Output 'API docs: http://127.0.0.1:8000/docs'
