# soak_run.ps1 - 一键无人值守 soak 启动器（跑完自动打印 RESULT）
#
# 用法（PowerShell 项目根目录）：
#   powershell -File tools\soak_run.ps1
#   powershell -File tools\soak_run.ps1 -Param 6 -Hours 2 -Pause 3
#   powershell -File tools\soak_run.ps1 -Until 16:00          # 跑到指定时刻
#
# 行为：确保 RTT 监听在跑 → 起 soak_3h.py → 控制台/stdout 双份日志 → 打印 RESULT。
# 中途查 RTT：powershell -File tools\rtt_check.ps1
param(
    [int]$Param = 6,
    [double]$Hours = 0,
    [string]$Until = "",
    [int]$Travel = 512000,
    [double]$Pause = 3,
    [double]$ZeroThresh = 1000000000,
    [int]$Tol = 1024
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

if ($Hours -le 0) {
    if ($Until -ne "") {
        $end = [datetime]::ParseExact((Get-Date).ToString("yyyy-MM-dd ") + $Until, "yyyy-MM-dd HH:mm", $null)
        if ($end -lt (Get-Date)) { $end = $end.AddDays(1) }
        $Hours = [math]::Round(($end - (Get-Date)).TotalHours, 3)
    } else {
        $Hours = 2.0
    }
}

# --- 确保 RTT 监听在跑（烧录与 RTT 互斥，故这里只查不改固件）---
$rttExe = 'C:\Program Files\SEGGER\JLink_V932\JLinkRTTLogger.exe'
$rttLog = Join-Path $root '.cl\capture\rtt.log'
if (-not (Get-Process JLinkRTTLogger -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $rttExe)) { throw "JLinkRTTLogger 不存在: $rttExe" }
    Start-Process -FilePath $rttExe -ArgumentList @(
        '-Device', 'STM32H750VB', '-If', 'SWD', '-Speed', '4000',
        '-RTTChannel', '0', $rttLog) -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-Host "[prep] RTT 监听已启动 -> $rttLog"
} else {
    Write-Host "[prep] RTT 监听已在运行"
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runLog = Join-Path $root ".cl\capture\soak_G${Param}_$stamp.log"
Write-Host "[prep] soak G$Param hours=$Hours travel=$Travel pause=$Pause  -> $runLog"
Write-Host "[prep] 中途体检 RTT: powershell -File tools\rtt_check.ps1"

$pyArgs = @(
    'tools/soak_3h.py',
    "--param=$Param",
    "--hours=$Hours",
    "--travel=$Travel",
    "--pause=$Pause",
    "--zero-thresh=$ZeroThresh",
    "--tol=$Tol"
)

# python 的 stderr 提示（如 uptime library not available）经 2>&1 会变成
# NativeCommandError，配 ErrorActionPreference=Stop 直接终止脚本 →
# 改用 2> 文件，既落盘又不进错误流。
$errLog = ($runLog -replace '\.log$', '.err.log')
$ErrorActionPreference = 'Continue'
& python @pyArgs 2> $errLog | Tee-Object -FilePath $runLog | ForEach-Object {
    $s = "$_"
    Write-Host $s
    if ($s -match 'RESULT:') { $script:RESULT = $s }
}
$ErrorActionPreference = 'Stop'

Write-Host "===================="
if ($script:RESULT) { Write-Host $script:RESULT } else { Write-Host "RESULT: 无输出(异常终止)" }
Write-Host "日志: $runLog"
Write-Host "RTT : $rttLog"
exit $LASTEXITCODE
