# 新增「不動產首購快報」月排程任務(2026-08-05，finfeed併入ip後的最後一步)
# 月排程(每月2日)——內政部實價登錄季資料約季末後30日才釋出，抓太頻繁沒
# 新資料。用pythonw.exe（今天稽核學到的教訓：一開始就用對，不要又用
# python.exe事後再修一輪彈窗問題）。
#
# 用法：以系統管理員身分開 PowerShell，執行本腳本。

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoRoot "venv\Scripts\pythonw.exe"
$mainPy = Join-Path $repoRoot "main.py"

if (-not (Test-Path $pythonw)) {
    Write-Error "找不到 $pythonw，確認venv位置正確"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$mainPy`" --source realestate_report"

# Windows排程原生沒有「每月固定日」的PowerShell cmdlet參數，用「每30天
# 重複」近似——會隨時間緩慢飄離每月2日（30天≠1個月），對這個用途(政府
# 季資料本身就有數週釋出時間差)精準度沒差，但如果之後真的要卡死在
# 每月2日，改用schtasks.exe的/sc monthly /d 2語法。
$startDate = (Get-Date -Day 2 -Hour 20 -Minute 0 -Second 0)
if ($startDate -lt (Get-Date)) { $startDate = $startDate.AddMonths(1) }
$trigger = New-ScheduledTaskTrigger -Once -At $startDate -RepetitionInterval (New-TimeSpan -Days 30) -RepetitionDuration ([TimeSpan]::MaxValue)

Register-ScheduledTask -TaskName "IntelPusher-RealestateReport" -Action $action -Trigger $trigger `
    -Description "不動產首購快報，月排程，2026-08-05從finfeed併入" -Force

Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName 'IntelPusher-RealestateReport' | Select TaskName,State"
Write-Host "  確認Actions.Execute是pythonw.exe（不是python.exe）"
