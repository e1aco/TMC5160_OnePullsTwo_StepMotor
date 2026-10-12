# rtt_check.ps1 - RTT 长时日志体检（查失步/故障位/饿狗）
#
# 用法：powershell -File tools\rtt_check.ps1
#       powershell -File tools\rtt_check.ps1 -Path .cl\capture\rtt.log
param(
    [string]$Path = ".cl\capture\rtt.log"
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Path)) { Write-Host "日志不存在: $Path"; exit 2 }

$lines = Get-Content $Path
Write-Host "文件: $Path  行数=$($lines.Count)  修改时间=$((Get-Item $Path).LastWriteTime)"

# [t=..] U1 act= enc= | U2 act= enc= ds= gs= es=
$tele = $lines | Where-Object { $_ -match '^\[t=\d+\] U1 act=' }
Write-Host "遥测行: $($tele.Count)"
if ($tele.Count -gt 0) { Write-Host ("最新: " + $tele[-1]) }

# 失步：ENC_STATUS es≠0 (deviation_warn) / GSTAT gs≠0 (drv_err/uv_cp/reset_err)
# DRV_STATUS 掩码严格对齐本工程 can_cmd.c CAN_CMD_Protect()：
#   bit26 OTPW / bit25 OT / bit27 S2GA / bit28 S2GB / bit12 S2VSA / bit13 S2VSB
#   → 0x1E003000。低10位 sg_result、bit16-23 的 CS_ACTUAL、bit24/30 等都是负载/电流
#   数据不是故障，若按通用 datasheet 位号会大量误报。
$esBad = $tele | Where-Object { $_ -match 'es=([0-9A-Fa-f]+)' -and [Convert]::ToInt32($Matches[1], 16) -ne 0 }
$gsBad = $tele | Where-Object { $_ -match 'gs=([0-9A-Fa-f]+)' -and [Convert]::ToInt32($Matches[1], 16) -ne 0 }
$dsBad = $tele | Where-Object { $_ -match 'ds=([0-9A-Fa-f]+)' -and ([Convert]::ToInt32($Matches[1], 16) -band 0x1E003000) }

Write-Host "---- 体检结果 ----"
Write-Host "es!=0 (失步 deviation_warn): $($esBad.Count)"
if ($esBad.Count -gt 0) { $esBad | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" } }
Write-Host "gs!=0 (GSTAT 故障):          $($gsBad.Count)"
if ($gsBad.Count -gt 0) { $gsBad | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" } }
Write-Host "ds 保护位命中(OTPW/OT/S2GA/S2GB/S2VSA/S2VSB): $($dsBad.Count)"
if ($dsBad.Count -gt 0) { $dsBad | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" } }

# 异常/复位痕迹
$bad = $lines | Where-Object { $_ -match 'FAIL|Error|error|HardFault|复位|assert|Overflow|stack' }
Write-Host "异常行: $($bad.Count)"
if ($bad.Count -gt 0) { $bad | Select-Object -Last 10 | ForEach-Object { Write-Host "  $_" } }

$fail = ($esBad.Count -eq 0) -and ($gsBad.Count -eq 0) -and ($dsBad.Count -eq 0) -and ($bad.Count -eq 0)
Write-Host "RTT_HEALTH: $(if ($fail) { 'OK' } else { 'NEED_REVIEW' })"
if (-not $fail) { exit 1 }
exit 0
