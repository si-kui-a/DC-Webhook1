# 新增「近期連結健康檢查」排程任務(2026-09-10建立)。
#
# scripts/check_links.py + scrapers/link_health.py + db.upsert_link_health()
# 早在2026-08-09(commit 288bc0c)就寫好且有測試(test/test_link_health.py)，
# 但從沒被排進任何排程——寫了邏輯卻沒有「定期真的跑」這一步，稽核時發現
# 補上。
#
# 用ops/run_task.ps1這個既有的「任務控制層」模板(ops/templates/README.md：
# 「讓排程器呼叫run_task.ps1，不要直接呼叫Python腳本」)，不是main.py的
# --source flag——check_links.py是獨立腳本，不在main.py的SOURCE_REGISTRY
# 體系裡，比照IntelPusher-CheckTriggers/IntelPusher-ResumeBot現有的接法。
# run_task.ps1會自動把stdout/stderr記進logs/runs/CheckLinks-*.log(.out/.err)，
# 不需要另外幫check_links.py加logging設定。
#
# 用法：以系統管理員身分開 PowerShell，執行本腳本。

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
$runTask = Join-Path $repoRoot "ops\run_task.ps1"

if (-not (Test-Path $python)) {
    Write-Error "找不到 $python，確認venv位置正確"
    exit 1
}
if (-not (Test-Path $runTask)) {
    Write-Error "找不到 $runTask"
    exit 1
}

$taskName = "IntelPusher-CheckLinks"
$taskRun = '"' + $runTask + '" -Task CheckLinks -Script scripts/check_links.py'

# 同add_thu_calendar_task.ps1/add_sig_content_watch_task.ps1的既有模式：
# 先局部降EAP查詢舊任務是否存在，查完立刻還原。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
schtasks /query /tn $taskName 2>&1 | Out-Null
$taskExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP

if ($taskExists) {
    schtasks /delete /tn $taskName /f | Out-Null
}

# 每週一早上5:00(既有排程裡最早的整點時段之前，避開跟06:00
# IntelPusher-ThuCalendar/07:00 IntelPusher-DailyRecap撞同一分鐘；
# check_links.py預設--days 7剛好覆蓋一週窗口，周期跟預設參數對齊)。
# 用powershell.exe -File呼叫run_task.ps1(而非直接呼叫python.exe)，
# 比照CheckTriggers/ResumeBot既有接法。
$powershellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$taskCmd = "`"$powershellExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $taskRun"
schtasks /create /tn $taskName /tr $taskCmd /sc weekly /d MON /st 05:00 /f

Write-Host ""
Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Select TaskName,State"
Write-Host "  執行後查看 logs/runs/CheckLinks-*.log(.out/.err)"
