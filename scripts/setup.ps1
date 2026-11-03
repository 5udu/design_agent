$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r backend/requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& '.\.venv\Scripts\python.exe' -m pip install --no-deps --no-build-isolation -e './backend'
if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed' }
Push-Location -LiteralPath 'frontend'
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
Write-Output 'Setup complete. Configure .env, then run .\scripts\start.ps1'
