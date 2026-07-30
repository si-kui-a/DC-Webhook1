# 需要以系統管理員身分執行的 PowerShell 執行。
# 建立模擬持倉+大總結頻道的5個新排程工作,比照既有IntelPusher-FedDaily/
# IntelPusher-Weekly的模式(venv python.exe + main.py --source X)。

$ProjectDir = 'C:\Projects\10-501_Intel_Pusher_股票情報推播機器人'
$Python = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$MainPy = Join-Path $ProjectDir 'main.py'

function New-IntelPusherTask {
    param($Name, $Source, $Trigger)
    $action = New-ScheduledTaskAction -Execute $Python -Argument "$MainPy --source $Source"
    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Force -ErrorAction Stop | Out-Null
        Write-Output "已建立: $Name"
    } catch {
        Write-Output "失敗: $Name -- $($_.Exception.Message)"
    }
}

# 大總結頻道(20:30,晚間彙整之後)
New-IntelPusherTask -Name 'IntelPusher-TwStockMeta' -Source 'tw_stock_meta' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:30PM)
New-IntelPusherTask -Name 'IntelPusher-CryptoMeta' -Source 'crypto_meta' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:30PM)

# 模擬持倉-台股(台股收盤後,工作日14:00)
New-IntelPusherTask -Name 'IntelPusher-TwStockPortfolio' -Source 'tw_stock_portfolio' `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 2:00PM)

# 模擬持倉-幣圈(每小時)
New-IntelPusherTask -Name 'IntelPusher-CryptoFuturesPortfolio' -Source 'crypto_futures_portfolio' `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650))
New-IntelPusherTask -Name 'IntelPusher-CryptoDiscretionaryPortfolio' -Source 'crypto_discretionary_portfolio' `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650))

Write-Output '完成,用 Get-ScheduledTask -TaskName "IntelPusher-*" 確認。'
