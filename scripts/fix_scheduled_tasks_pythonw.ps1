# 修正 IntelPusher 排程任務彈出主控台視窗的問題 + 清除重複/失效任務
# 2026-08-04 全面重寫（原本只處理3個任務，實際普查發現live排程有28個，
# 範圍/內容都跟原本認知不同，全部改依真實 Get-ScheduledTask 查詢結果重寫）
#
# 用法：以系統管理員身分開 PowerShell，執行本腳本。
# 執行前已把全部28個任務的現行XML備份到 scheduled_tasks_backup/，
# 如果任何一步結果不符預期，可以照備份手動復原（schtasks /create /xml ... /f）。

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 第一部分：python.exe -> pythonw.exe（21個任務，都是確認會繼續留用的任務）
# 原因：main.py 已有完整檔案log(activity.log/error.log)，換pythonw.exe
#       不會漏任何輸出，只是不再跳出主控台視窗。
# ---------------------------------------------------------------------------
$fixPythonw = @(
    "IntelPusher-CbcDigest","IntelPusher-CheckTriggers","IntelPusher-CryptoDigest",
    "IntelPusher-CryptoMeta","IntelPusher-CryptoNightlyRecap","IntelPusher-DailyRecap",
    "IntelPusher-Etf0050","IntelPusher-FedDaily","IntelPusher-GeopoliticsDigest",
    "IntelPusher-Internship","IntelPusher-MacroFred","IntelPusher-MacroTechDigest",
    "IntelPusher-ResumeBot","IntelPusher-Scholarship","IntelPusher-SemiSupplyChainDigest",
    "IntelPusher-TsmcDigest","IntelPusher-TwseChunghwa","IntelPusher-TwseTsmc",
    "IntelPusher-TwStockMeta","IntelPusher-TwStockPortfolio","IntelPusher-UsStockDigest",
    "IntelPusher_Weekly"
)

Write-Host "=== 第一部分：python.exe -> pythonw.exe（$($fixPythonw.Count)個任務）==="
foreach ($n in $fixPythonw) {
    $task = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $task) { Write-Warning "找不到任務: $n，跳過"; continue }
    $oldExe = $task.Actions[0].Execute
    if ($oldExe -notmatch 'python\.exe$') {
        Write-Warning "$n 的 Execute 不是預期的 python.exe（實際: $oldExe），跳過避免誤改"
        continue
    }
    $newExe = $oldExe -replace 'python\.exe$', 'pythonw.exe'
    $newAction = New-ScheduledTaskAction -Execute $newExe -Argument $task.Actions[0].Arguments
    Set-ScheduledTask -TaskName $n -Action $newAction | Out-Null
    Write-Host "  改好: $n -> pythonw.exe"
}

# ---------------------------------------------------------------------------
# 第二部分：刪除確認重複/失效的任務（已用 Get-ScheduledTaskInfo 逐一核對過
# 觸發排程跟LastRun/LastResult，不是單憑命名規律猜的）
# ---------------------------------------------------------------------------
# IntelPusher_Backup：跟 IntelPusher-Backup 觸發時間/程式完全相同，今天
#   同時間都成功跑過一次，純重複
# IntelPusher_FedDaily：跟 IntelPusher-FedDaily 同上，純重複
# IntelPusher-Weekly（連字號版）：只剩開機觸發，沒有真正的每週排程，
#   LastResult=267011（從未依排程執行過）——是壞掉/不完整的殘留任務，
#   真正在跑的是 IntelPusher_Weekly（底線版，上面已經修好pythonw了）
# scholarship-monitor-crawl / ScholarshipMonitor-Crawl：兩個完全重複，
#   指向已棄用的 scholarship-monitor 空殼倉庫（功能已併入ip），今天
#   兩個都跑了、兩個 LastResult 都是1（失敗）
$toDelete = @(
    "IntelPusher_Backup",
    "IntelPusher_FedDaily",
    "IntelPusher-Weekly",
    "scholarship-monitor-crawl",
    "ScholarshipMonitor-Crawl"
)

Write-Host ""
Write-Host "=== 第二部分：刪除確認重複/失效的任務（$($toDelete.Count)個）==="
Write-Host "（執行前已備份現行XML到 scheduled_tasks_backup/，如需復原可從那邊救回）"
foreach ($n in $toDelete) {
    $task = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $task) { Write-Warning "找不到任務: $n，跳過"; continue }
    Unregister-ScheduledTask -TaskName $n -Confirm:$false
    Write-Host "  已刪除: $n"
}

Write-Host ""
Write-Host "完成。驗證方式："
Write-Host "  Get-ScheduledTask -TaskName 'IntelPusher*' | Select TaskName,State"
Write-Host "  確認剩下的都是 IntelPusher-（連字號）系列 + IntelPusher_Weekly，"
Write-Host "  且都指向 pythonw.exe（IntelPusher-Backup 是bash.exe，本來就沒有這個問題）"
