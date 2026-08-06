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

# 排程精簡(2026-07-31)：晚間彙整7頻道+scholarship(原8個獨立任務)、
# 09:00官方資料源5個來源(原5個獨立任務)、20:30大總結2頻道(原2個獨立
# 任務)——全部改成main.py的--digest-all/--daily-official/--meta-all
# 批次flag，內部迴圈跑完同一時間點全部來源，Task Scheduler只留3個任務。
# 故意不在這裡逐一列舊的獨立任務定義，避免這支腳本之後重跑時把已經
# 精簡掉的舊任務重新建回來(同一個坑2026-07-31已在幣圈事件觸發那段
# 踩過一次，見下方模擬持倉幣圈的說明)。若需要重新套用，改跑
# scripts/consolidate_daily_tasks.ps1，不要在這個檔案裡加回逐一任務。
$actions2000 = @(
    New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --digest-all"
    New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --scholarship"
)
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Evening2000' -Action $actions2000 `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM) -Force -ErrorAction Stop | Out-Null
    Write-Output '已建立: IntelPusher-Evening2000'
} catch {
    Write-Output "失敗: IntelPusher-Evening2000 -- $($_.Exception.Message)"
}
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Morning0900' `
        -Action (New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --daily-official") `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM) -Force -ErrorAction Stop | Out-Null
    Write-Output '已建立: IntelPusher-Morning0900'
} catch {
    Write-Output "失敗: IntelPusher-Morning0900 -- $($_.Exception.Message)"
}
try {
    Register-ScheduledTask -TaskName 'IntelPusher-Evening2030' `
        -Action (New-ScheduledTaskAction -Execute $Python -Argument "`"$MainPy`" --meta-all") `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 8:30PM) -Force -ErrorAction Stop | Out-Null
    Write-Output '已建立: IntelPusher-Evening2030'
} catch {
    Write-Output "失敗: IntelPusher-Evening2030 -- $($_.Exception.Message)"
}

# 模擬持倉-台股(工作日14:40，2026-08-06改由14:00延後：tw_stock改用零股
# (BFT41U)當日成交價買進後，要等盤後零股單一價撮合(13:40-14:30)完成、
# 資料才會到位，14:00執行會撈到「今天還沒撮合完」而退回整股價，改到
# 14:40確保零股成交價已可查)
New-IntelPusherTask -Name 'IntelPusher-TwStockPortfolio' -Source 'tw_stock_portfolio' `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 2:40PM)

# 模擬持倉-幣圈：原本這裡是IntelPusher-CryptoFuturesPortfolio/
# IntelPusher-CryptoDiscretionaryPortfolio兩個每小時排程(各24次/天，
# 加起來48次/天)，2026-07-31發現GEMINI_API_KEY整包專案共用免費層額度
# 只有20次/天(429實測確認，見docs/gemini_quota_allocation.md)，這兩個
# 帳戶單獨就超過兩倍。已改用scripts/apply_event_triggered_crypto.ps1
# 註冊IntelPusher-CheckTriggers(每20分鐘跑純規則檢查，只有真的觸發
# 條件才呼叫AI)取代——故意不在這裡保留這兩個舊排程的定義，避免這支
# 腳本之後重跑時又把已經移除的每小時排程重新建回來(實測踩過這個坑：
# 重跑這支腳本後IntelPusher-CheckTriggers還在，但兩個舊的每小時排程
# 也被重新建立，變成三個排程同時對同一組帳戶動作)。若需要重新套用
# 事件觸發機制，改跑scripts/apply_event_triggered_crypto.ps1，不要
# 在這個檔案裡加回這兩行。

# 台灣實習頻道(每天09:00,daily不限工作日——職缺任何一天都可能新增)。
# 資料源(MOL台灣就業通開放資料)實測updateTime固定是每天01:00更新一次
# (見Meta_Dev_Knowledge.md PAT-18)，一天內多次執行只會重複處理同一份
# 快照、白白多打Gemini語意消歧的API，故只排一天一次，09:00讓資料確定
# 已更新完畢(比照twse_tsmc/twse_chunghwa等其他官方每日資料源的既有排程
# 時間)。
New-IntelPusherFlagTask -Name 'IntelPusher-Internship' -Flag 'internship' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM)

# 每日晨間快報(每天07:00,使用者2026-07-31確認的時間——早於所有其他
# 07:00後才開始的排程，讀取「昨天」已產出的大總結報告，見main.py
# run_daily_recap)。
New-IntelPusherTask -Name 'IntelPusher-DailyRecap' -Source 'daily_recap' `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00AM)

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
