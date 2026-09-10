# 新增「東海大學當期學期行事曆合併」排程任務(2026-09-10建立)。
# 取代原本教育類(scholarship/internship)的Telegram EDU bot通知——使用者
# 2026-09-10確認拿掉那三個Telegram簡短通知，改成這支每天早上6點跑的job，
# 把當期學期行事曆寫進data/thu_academic_calendar.json，交給backup.sh既有
# 的每日自動git commit+push機制一併帶進版控(見backup.sh的git add清單，
# 已於同次改動加入data/)。
#
# 用pythonw.exe(避免console彈窗，同realestate_report那次的教訓，見
# CLAUDE.md「Windows Environment Notes」)。
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

$taskName = "IntelPusher-ThuCalendar"
$taskRun = '"' + $pythonw + '" "' + $mainPy + '" --source thu_calendar'

# 同add_sig_content_watch_task.ps1的既有模式：先局部降EAP查詢舊任務是否
# 存在，查完立刻還原，避免「任務不存在」這種預期內訊息把整支腳本中斷。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
schtasks /query /tn $taskName 2>&1 | Out-Null
$taskExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP

if ($taskExists) {
    schtasks /delete /tn $taskName /f | Out-Null
}

# 每天早上6:00(使用者2026-09-10指定的時間)，早於所有其他排程(daily_recap
# 07:00、--daily-official 09:00)，避免搶同一分鐘。
schtasks /create /tn $taskName /tr $taskRun /sc daily /st 06:00 /f

Write-Host ""
Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Select TaskName,State"
Write-Host "  確認Actions.Execute是pythonw.exe（不是python.exe）"
