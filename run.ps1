# Start Nota on Windows.
#   .\run.ps1              -> http://127.0.0.1:8787
#   .\run.ps1 -Port 9000
param(
    [int]$Port = 8787,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not $Python) {
    # Prefer an interpreter that already has the dependencies.
    $candidates = @(
        "E:\application\dev\python311\python.exe",
        (Join-Path $root ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $Python = $candidate; break }
    }
    if (-not $Python) { $Python = "python" }
}

Write-Host "Nota" -ForegroundColor Green
Write-Host "  python : $Python"
Write-Host "  port   : $Port"

# Fail early with a readable message instead of a stack trace.
& $Python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "依赖缺失，请先运行：" -ForegroundColor Yellow
    Write-Host "  $Python -m pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "  模式   : demo（未找到 .env，使用确定性本地规划器）" -ForegroundColor Cyan
}

$env:PORT = "$Port"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $root
try {
    & $Python -X utf8 -m backend.main
}
finally {
    Pop-Location
}
