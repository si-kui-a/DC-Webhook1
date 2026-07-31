# 需要以系統管理員身分執行的 PowerShell 執行。
#
# 排程精簡(2026-07-31,使用者確認)：原本22個Windows Scheduled Task裡有
# 15個其實是「同一個時間點、各自獨立」的來源，main.py已新增--digest-all/
# --meta-all/--daily-official三個批次flag(內部迴圈跑完同一時間點全部
# 來源)，這裡把對應的舊任務移除、換成3個新任務。
#
# 20:00群組(晚間彙整,8個舊任務->1個新任務,2個Action循序執行)：
#   TsmcDigest/CbcDigest/UsStockDigest/CryptoDigest/MacroTechDigest/
#   GeopoliticsDigest/SemiSupplyChainDigest(--digest-all涵蓋) + Scholarship
#   (--scholarship,不同函式無法併入--digest-all，改用同一個Task的第二個
#   Action循序執行，Windows Scheduled Task本來就支援多個Action)
# 09:00群組(官方資料源,5個舊任務->1個新任務)：
#   FedDaily/MacroFred/TwseTsmc/TwseChunghwa/Etf0050(--daily-official涵蓋，
#   macro_fred/etf0050原本只排平日，改成每天執行不影響功能——非交易日
#   執行只會抓到空資料靜默省略，是這幾個來源既有的容錯設計)
# 20:30群組(大總結,2個舊任務->1個新任務)：
#   TwStockMeta/CryptoMeta(--meta-all涵蓋)
#
# crypto_futures_portfolio/crypto_discretionary_portfolio(已改事件觸發，
# 見apply_event_triggered_crypto.ps1)、Internship(09:00但跟這三組不同
# 時間點/獨立語意)、Backup、ResumeBot(AtLogOn常駐)都不在本次調整範圍。

$ProjectDir = 'C:\Projects\10-501_Intel_Pusher_股票情報推播機器人'
$Python = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$MainPy = Join-Path $ProjectDir 'main.py'

$oldTaskNames = @(
    'IntelPusher-TsmcDigest', 'IntelPusher-CbcDigest', 'IntelPusher-UsStockDigest',
    'IntelPusher-CryptoDigest', 'IntelPusher-MacroTechDigest', 'IntelPusher-GeopoliticsDigest',
    'IntelPusher-SemiSupplyChainDigest', 'IntelPusher-Scholarship',
    'IntelPusher-FedDaily', 'IntelPusher-MacroFred', 'IntelPusher-TwseTsmc',
    'IntelPusher-TwseChunghwa', 'IntelPusher-Etf0050',
    'IntelPusher-TwStockMeta', 'IntelPusher-CryptoMeta',
    'IntelPusher-Weekly'
)
foreach ($name in $oldTaskNames) {
    try {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Output "已移除舊排程: $name"
    } catch {
        Write-Output "移除舊排程時發生問題(可能本來就不存在,可忽略): $name -- $($_.Exception.Message)"
    }
}

# 20:00群組：兩個Action循序執行(--digest-all先跑完7個彙整頻道，再跑scholarship)
$actions2000 = @(
    New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --digest-all"
    New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --scholarship"
)
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Evening2000' -Action $actions2000 `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM) -Force -ErrorAction Stop | Out-Null
    Write-Output "已建立: IntelPusher-Evening2000 (20:00,7個彙整頻道+scholarship)"
} catch {
    Write-Output "失敗: IntelPusher-Evening2000 -- $($_.Exception.Message)"
}

# 09:00群組
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Morning0900' `
        -Action (New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --daily-official") `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM) -Force -ErrorAction Stop | Out-Null
    Write-Output "已建立: IntelPusher-Morning0900 (09:00,fed/etf0050/macro_fred/twse_tsmc/twse_chunghwa)"
} catch {
    Write-Output "失敗: IntelPusher-Morning0900 -- $($_.Exception.Message)"
}

# 20:30群組
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Evening2030' `
        -Action (New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --meta-all") `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 8:30PM) -Force -ErrorAction Stop | Out-Null
    Write-Output "已建立: IntelPusher-Evening2030 (20:30,tw_stock_meta+crypto_meta)"
} catch {
    Write-Output "失敗: IntelPusher-Evening2030 -- $($_.Exception.Message)"
}

Write-Output ""
Write-Output '完成，用 Get-ScheduledTask -TaskName "IntelPusher-*" 確認。'