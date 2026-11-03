param([switch]$Force)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Force) {
    try {
        $projects = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/projects' -TimeoutSec 3
        foreach ($project in $projects) {
            $detail = Invoke-RestMethod -Uri ('http://127.0.0.1:8000/api/projects/'+$project.id) -TimeoutSec 3
            if (@($detail.runs | Where-Object { $_.status -in @('queued','running') }).Count -gt 0) { throw 'Active design task: wait for completion before stopping.' }
        }
    } catch {
        if ($_.Exception.Message -like 'Active design task:*') { throw }
    }
}
foreach ($serviceName in @('worker','api','ocr')) {
    $pidFile = Join-Path $projectRoot ('data\logs\'+$serviceName+'.pid')
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $servicePid = [int](Get-Content -LiteralPath $pidFile)
    $serviceProcess = Get-CimInstance Win32_Process -Filter ('ProcessId='+$servicePid)
    if (-not $serviceProcess) { continue }
    $owned = $serviceProcess.ExecutablePath -like ($projectRoot+'\.venv\*') -or $serviceProcess.ExecutablePath -like ($projectRoot+'\.ocr-venv\*')
    if (-not $owned) { throw ('PID belongs to another process; refused: '+$servicePid) }
    Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$servicePid) | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $servicePid -ErrorAction SilentlyContinue
}
Write-Output 'Project services stopped; saved projects and files are preserved.'
