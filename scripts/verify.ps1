# Start Nota and smoke-test the whole HTTP surface.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1
# Exits non-zero if any check fails.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = if ($env:NOTA_PYTHON) { $env:NOTA_PYTHON } else { "E:\application\dev\python311\python.exe" }
if (-not (Test-Path $python)) { $python = "python" }

$port = if ($env:NOTA_PORT) { [int]$env:NOTA_PORT } else { 8791 }
$base = "http://127.0.0.1:$port"
$tempDb = Join-Path $root "data\verify.db"
$tempData = Join-Path $root "data\verify"
foreach ($p in @($tempDb, "$tempDb-wal", "$tempDb-shm")) {
    if (Test-Path $p) { Remove-Item $p -Force }
}
if (Test-Path $tempData) { Remove-Item $tempData -Recurse -Force }
New-Item -ItemType Directory -Force -Path $tempData | Out-Null

$env:DB_PATH = $tempDb
$env:DATA_DIR = $tempData
$env:PORT = "$port"
$env:LLM_PROVIDER = "scripted"
$env:STT_PROVIDER = "noop"
$env:HOST = "127.0.0.1"
$env:PYTHONIOENCODING = "utf-8"

$failures = 0
function Check($name, $condition, $detail) {
    if ($condition) {
        Write-Host "  OK   $name" -ForegroundColor Green
    } else {
        Write-Host "  FAIL $name $detail" -ForegroundColor Red
        $script:failures++
    }
}

# Windows PowerShell encodes a string body as Latin-1, which turns every Chinese
# character into '?'. Always post explicit UTF-8 bytes.
function Send-Json($path, $payload) {
    $json = if ($payload -is [string]) { $payload } else { $payload | ConvertTo-Json -Compress }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri ($base + $path) -Method Post -Body $bytes `
        -ContentType "application/json; charset=utf-8" `
        -Headers @{ "X-User-Id" = "verify" }
}

function Get-Api($path) {
    return Invoke-RestMethod -Uri ($base + $path) -Headers @{ "X-User-Id" = "verify" }
}

Write-Host "starting backend on $base ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $python -ArgumentList "-X", "utf8", "-m", "backend.main" `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $tempData "stdout.log") `
    -RedirectStandardError (Join-Path $tempData "stderr.log")

try {
    $ready = $false
    for ($i = 0; $i -lt 80; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$base/healthz" -TimeoutSec 2
            if ($health.ok) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) {
        Write-Host "server did not come up; stderr:" -ForegroundColor Red
        Get-Content (Join-Path $tempData "stderr.log") -ErrorAction SilentlyContinue
        exit 1
    }

    Write-Host ""
    Write-Host "[1] health" -ForegroundColor Cyan
    Check "healthz.ok" ($health.ok -eq $true) ""
    Check "provider is scripted" ($health.provider -eq "scripted") $health.provider

    Write-Host ""
    Write-Host "[2] static web page" -ForegroundColor Cyan
    $index = Invoke-WebRequest -Uri "$base/" -TimeoutSec 5 -UseBasicParsing
    Check "GET / -> 200" ($index.StatusCode -eq 200) $index.StatusCode
    Check "login page has the form" ($index.Content -match "login-form") ""
    $console = Invoke-WebRequest -Uri "$base/console" -TimeoutSec 5 -UseBasicParsing
    Check "GET /console -> 200" ($console.StatusCode -eq 200) $console.StatusCode
    $trace = Invoke-WebRequest -Uri "$base/trace" -TimeoutSec 5 -UseBasicParsing
    Check "GET /trace -> 200" ($trace.StatusCode -eq 200) $trace.StatusCode
    Check "trace page still has the result column" ($trace.Content -match "整理结果") ""
    $js = Invoke-WebRequest -Uri "$base/js/api.js" -TimeoutSec 5 -UseBasicParsing
    Check "GET /js/api.js -> 200" ($js.StatusCode -eq 200) $js.StatusCode
    $blocked = $false
    try {
        Invoke-WebRequest -Uri "$base/js/../.env" -TimeoutSec 5 -UseBasicParsing | Out-Null
    } catch {
        $blocked = $true
    }
    Check "static routes reject path traversal" $blocked ""

    Write-Host ""
    Write-Host "[2b] back office login and roles" -ForegroundColor Cyan
    $anonymous = $false
    try {
        Invoke-WebRequest -Uri "$base/api/admin/overview" -TimeoutSec 5 -UseBasicParsing | Out-Null
    } catch {
        $anonymous = $true
    }
    Check "admin API requires a session" $anonymous ""

    $loginBody = @{ username = "admin"; password = "123456" } | ConvertTo-Json
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $login = Invoke-WebRequest -Uri "$base/api/auth/login" -Method Post `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($loginBody)) `
        -ContentType "application/json" -WebSession $session -UseBasicParsing -TimeoutSec 10
    Check "admin login -> 200" ($login.StatusCode -eq 200) $login.StatusCode
    $me = Invoke-RestMethod -Uri "$base/api/auth/me" -WebSession $session -TimeoutSec 5
    Check "session identifies the admin" ($me.user.role -eq "admin") $me.user.role
    $overview = Invoke-RestMethod -Uri "$base/api/admin/overview" -WebSession $session -TimeoutSec 10
    Check "admin overview returns accounts" ($overview.totals.users -ge 6) $overview.totals.users
    $users = Invoke-RestMethod -Uri "$base/api/admin/users" -WebSession $session -TimeoutSec 10
    Check "five mock users plus admin" (@($users.users).Count -ge 6) (@($users.users).Count)

    # ASCII on purpose: Windows PowerShell 5.1 decodes a JSON response with no
    # charset as Latin-1, so a Chinese comparison would fail on the harness rather
    # than on the server. Exact UTF-8 handling is covered by pytest.
    $filedBody = @{ title = "verify-admin-todo"; due_at = "2026-12-01T09:00" } | ConvertTo-Json
    $filed = Invoke-RestMethod -Uri "$base/api/admin/users/u_zhangwei/todos" -Method Post `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($filedBody)) `
        -ContentType "application/json" -WebSession $session -TimeoutSec 10
    Check "admin can file a todo for a user" ($filed.todo.title -eq "verify-admin-todo") $filed.todo.title
    Check "filed todo carries a timezone" ($filed.todo.due_at -like "*+08:00") $filed.todo.due_at

    # The mobile path: register creates an account and hands back a token, which
    # the app then sends as a Bearer header instead of a cookie.
    $newUser = "verify_" + (Get-Random -Maximum 999999)
    $registerBody = @{ username = $newUser; password = "verify123" } | ConvertTo-Json
    $registered = Invoke-RestMethod -Uri "$base/api/auth/register" -Method Post `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($registerBody)) `
        -ContentType "application/json" -TimeoutSec 10
    Check "register returns a token" (-not [string]::IsNullOrWhiteSpace($registered.token)) ""
    Check "registered account is a plain user" ($registered.user.role -eq "user") $registered.user.role

    $bearer = @{ Authorization = "Bearer " + $registered.token }
    $mobileQuota = Invoke-RestMethod -Uri "$base/api/quota" -Headers $bearer -TimeoutSec 5
    Check "bearer token works like a session" ($mobileQuota.limit -eq 10) $mobileQuota.limit
    $blockedAdmin = $false
    try {
        Invoke-WebRequest -Uri "$base/api/admin/overview" -Headers $bearer -UseBasicParsing | Out-Null
    } catch {
        $blockedAdmin = $true
    }
    Check "bearer token cannot reach the admin API" $blockedAdmin ""

    $logout = Invoke-WebRequest -Uri "$base/api/auth/logout" -Method Post `
        -WebSession $session -UseBasicParsing -TimeoutSec 5
    Check "logout -> 200" ($logout.StatusCode -eq 200) $logout.StatusCode

    Write-Host ""
    Write-Host "[3] quota starts at 0/10" -ForegroundColor Cyan
    $quota = Get-Api "/api/quota"
    Check "used=0" ($quota.used -eq 0) $quota.used
    Check "limit=10" ($quota.limit -eq 10) $quota.limit

    Write-Host ""
    Write-Host "[4] text note -> summary + todo + trace" -ForegroundColor Cyan
    $created = Send-Json "/api/notes/text" @{
        text            = "明天下午三点提醒我给张总发报价单"
        idempotency_key = "verify-1"
    }
    Check "job created" ($null -ne $created.job_id) ""

    $job = $null
    for ($i = 0; $i -lt 100; $i++) {
        $job = Get-Api ("/api/jobs/" + $created.job_id)
        if ($job.state -eq "done" -or $job.state -eq "failed") { break }
        Start-Sleep -Milliseconds 300
    }
    Check "job state = done" ($job.state -eq "done") $job.state
    Check "has summary" (-not [string]::IsNullOrWhiteSpace($job.summary)) ""
    Check "has todo" (@($job.todos).Count -ge 1) (@($job.todos).Count)
    Check "has reminder" (@($job.reminders).Count -ge 1) (@($job.reminders).Count)
    $due = if (@($job.todos).Count -ge 1) { $job.todos[0].due_at } else { "" }
    # "明天下午三点" is relative, so the expected date has to be computed. Hardcoding
    # it made this check pass one day and fail the next.
    $tomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
    Check "todo due $tomorrow`T15:00" ($due -like "$tomorrow`T15:00*") $due
    $fire = if (@($job.reminders).Count -ge 1) { $job.reminders[0].fire_at } else { "" }
    Check "reminder fires $tomorrow`T15:00" ($fire -like "$tomorrow`T15:00*") $fire

    $steps = Get-Api ("/api/jobs/" + $created.job_id + "/steps")
    Check "trace has assistant step" (@($steps | Where-Object { $_.type -eq "assistant" }).Count -ge 1) ""
    Check "trace has tool step" (@($steps | Where-Object { $_.type -eq "tool_result" }).Count -ge 1) ""

    Write-Host ""
    Write-Host "[5] idempotency: same key is free" -ForegroundColor Cyan
    $replay = Send-Json "/api/notes/text" @{
        text            = "明天下午三点提醒我给张总发报价单"
        idempotency_key = "verify-1"
    }
    Check "replayed=true" ($replay.replayed -eq $true) $replay.replayed
    $quota2 = Get-Api "/api/quota"
    Check "quota still 1" ($quota2.used -eq 1) $quota2.used

    Write-Host ""
    Write-Host "[6] history Q&A cites a note" -ForegroundColor Cyan
    $answer = Send-Json "/api/ask" @{ question = "上次给张总的报价单是什么" }
    Check "answer not empty" (-not [string]::IsNullOrWhiteSpace($answer.answer)) ""
    Check "grounded" ($answer.grounded -eq $true) $answer.grounded
    Check "retrieved > 0" ($answer.retrieved -gt 0) $answer.retrieved
    Check "has citation" (@($answer.citations).Count -ge 1) (@($answer.citations).Count)

    Write-Host ""
    Write-Host "[7] free tier: the 11th request is rejected" -ForegroundColor Cyan
    for ($i = 2; $i -le 10; $i++) {
        Send-Json "/api/notes/text" @{ text = "待办第 $i 条"; idempotency_key = "verify-$i" } | Out-Null
    }
    $blocked = $false
    $status = 0
    try {
        Send-Json "/api/notes/text" @{ text = "第十一条"; idempotency_key = "verify-11" } | Out-Null
    } catch {
        $status = [int]$_.Exception.Response.StatusCode
        $blocked = ($status -eq 429)
    }
    Check "11th request -> 429" $blocked "status=$status"
    $quota3 = Get-Api "/api/quota"
    Check "quota capped at 10" ($quota3.used -eq 10) $quota3.used

    Write-Host ""
    Write-Host "[8] device endpoint" -ForegroundColor Cyan
    $dev = Send-Json "/api/device/command" @{ machine_code = "A1B2C3D4E5F6"; text = "明天买牛奶" }
    Check "device command accepted" ($dev.ok -eq $true) "$($dev.error)"
    $status2 = Invoke-RestMethod -Uri "$base/api/device/A1B2C3D4E5F6/status"
    Check "device bound to a user" (-not [string]::IsNullOrWhiteSpace($status2.user)) ""
}
finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Start-Sleep -Milliseconds 300
}

Write-Host ""
if ($failures -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "$failures CHECK(S) FAILED" -ForegroundColor Red
    Get-Content (Join-Path $tempData "stderr.log") -ErrorAction SilentlyContinue | Select-Object -Last 25
    exit 1
}
