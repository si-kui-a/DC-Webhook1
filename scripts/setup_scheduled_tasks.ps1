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

function New-IntelPusherFlagTask {
    # --scholarship/--internship是布林flag，不是--source X的形式，另開一個
    # helper而不是硬改New-IntelPusherTask的參數形狀。
    param($Name, $Flag, $Trigger)
    $action = New-ScheduledTaskAction -Execute $Python -Argument "$MainPy --$Flag"
    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Force -ErrorAction Stop | Out-Null
        Write-Output "已建立: $Name"
    } catch {
        Write-Output "失敗: $Name -- $($_.Exception.Message)"
    }
}

# 晚間彙整頻道(20:00,tw_stock_meta/crypto_meta的前置依賴)。2026-07-30
# 發現這6個從專案初期就存在的頻道從來沒有被排程過(crontab.example移除
# 時漏補到Task Scheduler)，導致tw_stock_meta/crypto_meta讀到的一直是
# 舊資料——見Meta_Dev_Knowledge.md相關記錄。
New-IntelPusherTask -Name 'IntelPusher-TsmcDigest' -Source 'tsmc_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-CbcDigest' -Source 'cbc_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-UsStockDigest' -Source 'us_stock_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-CryptoDigest' -Source 'crypto_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-MacroTechDigest' -Source 'macro_tech_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-GeopoliticsDigest' -Source 'geopolitics_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherFlagTask -Name 'IntelPusher-Scholarship' -Flag 'scholarship' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)
New-IntelPusherTask -Name 'IntelPusher-SemiSupplyChainDigest' -Source 'semi_supply_chain_digest' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)

# 官方每日開放資料來源(同樣是crontab.example移除時漏補的既有功能，
# 9:00比照原本crontab.example的時間)。
New-IntelPusherTask -Name 'IntelPusher-MacroFred' -Source 'macro_fred' `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:00AM)
New-IntelPusherTask -Name 'IntelPusher-TwseTsmc' -Source 'twse_tsmc' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM)
New-IntelPusherTask -Name 'IntelPusher-TwseChunghwa' -Source 'twse_chunghwa' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM)

# etf0050(股價追蹤+三大法人買賣超T86"日報"+均線支撐,2026-07-30發現的
# 排程缺口)：程式碼註解明確寫是每個交易日性質的資料，原本只靠
# IntelPusher-Weekly(每週一一次)順便覆蓋，法人動向等於一週才更新一次，
# 跟資料本身的日頻更新不符。比照macro_fred同為平日9:00(非交易日執行
# 只會抓到空資料，靜默省略，不影響其餘功能，見etf0050.py既有容錯設計)。
New-IntelPusherTask -Name 'IntelPusher-Etf0050' -Source 'etf0050' `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:00AM)

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

# 台灣實習頻道(每天09:00,daily不限工作日——職缺任何一天都可能新增)。
# 資料源(MOL台灣就業通開放資料)實測updateTime固定是每天01:00更新一次
# (見Meta_Dev_Knowledge.md PAT-18)，一天內多次執行只會重複處理同一份
# 快照、白白多打Gemini語意消歧的API，故只排一天一次，09:00讓資料確定
# 已更新完畢(比照twse_tsmc/twse_chunghwa等其他官方每日資料源的既有排程
# 時間)。
New-IntelPusherFlagTask -Name 'IntelPusher-Internship' -Flag 'internship' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM)

# 履歷配對Discord常駐bot(2026-07-31新增)。跟以上所有任務都不同性質——
# 這支是要「一直開著」的WebSocket連線process，不是排程批次執行一次就
# 結束，故用AtLogOn觸發(登入時啟動一次)+RestartCount設定(當機/斷線
# 意外結束時自動重啟，最多重試99次、間隔1分鐘，避免需要人工介入重開)。
# 前置需求：DISCORD_BOT_TOKEN對應的bot需先在Discord Developer Portal
# 手動啟用「Message Content Intent」(privileged intent，API無法自動開)。
$resumeBotAction = New-ScheduledTaskAction -Execute $Python -Argument (Join-Path $ProjectDir 'resume_bot.py')
$resumeBotSettings = New-ScheduledTaskSettingsSet -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0)
try {
    Register-ScheduledTask -TaskName 'IntelPusher-ResumeBot' -Action $resumeBotAction `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Settings $resumeBotSettings -Force -ErrorAction Stop | Out-Null
    Write-Output '已建立: IntelPusher-ResumeBot'
} catch {
    Write-Output "失敗: IntelPusher-ResumeBot -- $($_.Exception.Message)"
}

Write-Output '完成,用 Get-ScheduledTask -TaskName "IntelPusher-*" 確認。'
