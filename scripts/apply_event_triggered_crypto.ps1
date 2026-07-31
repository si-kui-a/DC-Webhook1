# 需要以系統管理員身分執行的 PowerShell 執行(本session沒有管理員權限,
# 無法直接修改Scheduled Tasks,見全域CLAUDE.md已知限制)。
#
# 加密貨幣模擬持倉「事件觸發」機制(2026-07-31,使用者APPROVED,同日再次
# 確認擴及crypto_discretionary_portfolio)——把原本每小時無條件呼叫AI的
# IntelPusher-CryptoFuturesPortfolio + IntelPusher-CryptoDiscretionaryPortfolio
# 兩個排程，換成同一支check_triggers.py每20分鐘跑一次(純規則,零AI,內部
# 迴圈檢查兩個帳戶),只有真的觸發數值面/消息面/市場面/保底機制任一條件
# 才會間接呼叫main.py做AI決策。
#
# 背景:這兩個帳戶原本各自每小時=24次/天，加起來48次/天，但GEMINI_API_KEY
# 免費層配額整個ip專案共用只有20次/天(2026-07-31實測HTTP 429確認)，這兩個
# 帳戶加起來就超過整包配額兩倍多，這是實測發現的問題不是理論推測。
#
# 執行後效果:
# 1. 移除IntelPusher-CryptoFuturesPortfolio + IntelPusher-CryptoDiscretionaryPortfolio
#    兩個舊的每小時排程
# 2. 新增IntelPusher-CheckTriggers每20分鐘排程(呼叫check_triggers.py,內部
#    同時處理兩個帳戶)
# tw_stock_portfolio的排程不受影響。

$ProjectDir = 'C:\Projects\10-501_Intel_Pusher_股票情報推播機器人'
$Python = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$CheckTriggersPy = Join-Path $ProjectDir 'check_triggers.py'

$oldTaskNames = @('IntelPusher-CryptoFuturesPortfolio', 'IntelPusher-CryptoDiscretionaryPortfolio')
foreach ($oldTaskName in $oldTaskNames) {
    try {
        Unregister-ScheduledTask -TaskName $oldTaskName -Confirm:$false -ErrorAction Stop
        Write-Output "已移除舊排程: $oldTaskName"
    } catch {
        Write-Output "移除舊排程時發生問題(可能本來就不存在,可忽略): $oldTaskName -- $($_.Exception.Message)"
    }
}

$newTaskName = 'IntelPusher-CheckTriggers'
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$CheckTriggersPy`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 20) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
try {
    Register-ScheduledTask -TaskName $newTaskName -Action $action -Trigger $trigger -Force -ErrorAction Stop | Out-Null
    Write-Output "已建立新排程: $newTaskName (每20分鐘執行check_triggers.py,同時處理futures+discretionary兩個帳戶)"
} catch {
    Write-Output "建立新排程失敗: $newTaskName -- $($_.Exception.Message)"
}