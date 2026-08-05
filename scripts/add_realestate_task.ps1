# 新增「不動產首購快報」月排程任務(2026-08-05，finfeed併入ip後的最後一步)
# 月排程(每月2日)——內政部實價登錄季資料約季末後30日才釋出，抓太頻繁沒
# 新資料。用pythonw.exe（今天稽核學到的教訓：一開始就用對，不要又用
# python.exe事後再修一輪彈窗問題）。
#
# 2026-08-05修正：原本用New-ScheduledTaskTrigger的-RepetitionDuration
# ([TimeSpan]::MaxValue)想做「每30天重複、永不停止」，但Task Scheduler的
# XML schema對Duration有上限，MaxValue換算出來的P99999999DT23H59M59S超出
# 範圍，Register-ScheduledTask直接報錯（實測撞到）。改用schtasks.exe原生
# 的/sc monthly /d 2語法——這是Task Scheduler真正支援「每月固定某一天」
# 的排程類型，不用「每30天重複」硬湊，也沒有這個上限問題，順便解決舊版
# 本來就承認的「30天≠1個月，會逐月飄移」瑕疵。
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

$taskName = "IntelPusher-RealestateReport"
# 2026-08-05實測驗證過：這個單引號字串串接寫法，schtasks.exe能正確把它
# 拆成獨立的Command/Arguments兩個欄位（用一個丟棄的測試任務跑
# schtasks /query /xml確認過，不是憑印象假設）。
$taskRun = '"' + $pythonw + '" "' + $mainPy + '" --source realestate_report'

# 先移除同名舊任務（若上次失敗前有殘留），避免schtasks /create報「工作已存在」
schtasks /query /tn $taskName > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    schtasks /delete /tn $taskName /f | Out-Null
}

schtasks /create /tn $taskName /tr $taskRun /sc monthly /d 2 /st 20:00 /f

Write-Host ""
Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Select TaskName,State"
Write-Host "  確認Actions.Execute是pythonw.exe（不是python.exe）"
