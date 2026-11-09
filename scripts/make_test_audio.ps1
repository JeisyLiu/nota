# 用 Windows 自带的中文语音合成生成一段测试音频，方便本地验证 /api/notes/audio。
#
#   .\scripts\make_test_audio.ps1
#   .\scripts\make_test_audio.ps1 -Text "后天上午十点跟设计团队评审原型" -Out data\samples\demo.wav
#
# 依赖：Windows 10+ 的 SAPI（无需安装）。中文语音通常是 "Microsoft Huihui Desktop"，
# 如果机器上没有中文语音，脚本会列出可用语音并退出。

param(
    [string]$Text = "明天下午三点提醒我给张总发报价单，还有把上周的会议纪要整理一下。",
    [string]$Out = "data\samples\demo.wav",
    [string]$Voice = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# --- 选语音 ---------------------------------------------------------------
$synth = New-Object -ComObject SAPI.SpVoice
$voices = @($synth.GetVoices())

if (-not $Voice) {
    $zh = $voices | Where-Object { $_.GetDescription() -match "Chinese|Huihui|Xiaoxiao|Kangkang|Yaoyao" }
    if ($zh) {
        $Voice = ($zh | Select-Object -First 1).GetDescription()
    }
}

if (-not $Voice) {
    Write-Host "没有找到中文语音包。机器上可用的语音：" -ForegroundColor Yellow
    $voices | ForEach-Object { Write-Host ("  - " + $_.GetDescription()) }
    Write-Host ""
    Write-Host "可以手动指定，例如：" -ForegroundColor Cyan
    Write-Host '  .\scripts\make_test_audio.ps1 -Voice "Microsoft Huihui Desktop"'
    exit 1
}

Write-Host "语音: $Voice"
Write-Host "文本: $Text"

# --- 合成到内存，再写成 16kHz/16bit/mono WAV -------------------------------
$stream = New-Object -ComObject SAPI.SpFileStream
$format = New-Object -ComObject SAPI.SpAudioFormat
# SAFT16kHz16BitMono = 18
$format.Type = 18

$target = Join-Path $root $Out
$dir = Split-Path -Parent $target
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

$stream.Format = $format
# 3 = SSFMCreateForWrite
$stream.Open($target, 3, $false)
$synth.AudioOutputStream = $stream
$synth.Speak($Text) | Out-Null
$stream.Close()

$info = Get-Item $target
Write-Host ""
Write-Host ("已生成 {0}  ({1:N1} KB)" -f $target, ($info.Length / 1KB)) -ForegroundColor Green
Write-Host ""
Write-Host "上传验证：" -ForegroundColor Cyan
Write-Host ('  curl.exe -X POST "http://127.0.0.1:8787/api/notes/audio" -H "X-User-Id: demo" -F "file=@' + $target + '"')
