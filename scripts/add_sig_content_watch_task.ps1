# 新增「留德網站內容維護監測」排程任務(2026-08-05建立，同日改為每季一次)
# 官方連結/學校網址變動頻率低，季排程夠用。用pythonw.exe(避免console彈窗，
# 同realestate_report那次的教訓)。
#
# 每季一次的排法：schtasks的MONTHLY排程用/M指定月份(1,4,7,10月)+/D指定
# 該月第幾天，這是原生支援「每N個月」的正確寫法，不是用RepetitionDuration
# 硬湊「每90天重複」(那個做法在realestate_report那次已經撞過XML schema
# 上限的坑，見add_realestate_task.ps1的說明)。
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

$taskName = "IntelPusher-SigContentWatch"
$taskRun = '"' + $pythonw + '" "' + $mainPy + '" --source sig_content_watch'

# 同realestate_report那支腳本的既有模式：先局部降EAP查詢舊任務是否存在，
# 查完立刻還原，避免「任務不存在」這種預期內訊息把整支腳本中斷。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
schtasks /query /tn $taskName 2>&1 | Out-Null
$taskExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP

if ($taskExists) {
    schtasks /delete /tn $taskName /f | Out-Null
}

# 每季(1/4/7/10月)2號早上9:30，避開跟其他09:00批次任務(--daily-official)撞同一分鐘。
schtasks /create /tn $taskName /tr $taskRun /sc monthly /m JAN,APR,JUL,OCT /d 2 /st 09:30 /f

Write-Host ""
Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Select TaskName,State"
Write-Host "  確認Actions.Execute是pythonw.exe（不是python.exe）"
