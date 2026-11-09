# Start Nota on Windows.
#   .\run.ps1              -> http://127.0.0.1:8787
#   .\run.ps1 -Port 9000
#   .\run.ps1 -Demo        -> force the offline planner even if .env has a key
param(
    [int]$Port = 8787,
    [string]$Python = "",
    [switch]$Demo
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

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "  模式   : demo（未找到 .env，使用确定性本地规划器）" -ForegroundColor Cyan
    Write-Host "           要接真模型：Copy-Item .env.example .env 然后填 key" -ForegroundColor DarkGray
}

if ($Demo) {
    # Process env beats .env (load_dotenv uses override=False), so this wins
    # without editing the file. Handy for a quick offline check.
    $env:LLM_PROVIDER = "scripted"
    Write-Host "  模式   : demo（-Demo 强制离线规划器）" -ForegroundColor Cyan
}

$env:PORT = "$Port"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $root
try {
    & $Python -X utf8 -m backend.main
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -eq 2) {
    Write-Host ""
    Write-Host "启动失败：模型配置有问题（见上方说明）。" -ForegroundColor Yellow
    Write-Host "  - 填好 .env 里的 OPENAI_API_KEY 后重试"
    Write-Host "  - 先自检：$Python -m scripts.check_llm"
    Write-Host "  - 只想验证链路：.\run.ps1 -Demo"
    exit 2
}
exit $code
