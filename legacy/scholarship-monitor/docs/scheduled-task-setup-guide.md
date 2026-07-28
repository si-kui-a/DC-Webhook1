# 排程任務完整設定教學

## 目錄

1. [開啟系統管理員 PowerShell](#1-開啟系統管理員-powershell)
2. [一次匯入所有排程（推薦）](#2-一次匯入所有排程推薦)
3. [逐一匯入（選擇性）](#3-逐一匯入選擇性)
4. [驗證設定成功](#4-驗證設定成功)
5. [測試關機自動補跑](#5-測試關機自動補跑)
6. [排程任務說明總表](#6-排程任務說明總表)

---

## 1. 開啟系統管理員 PowerShell

**Windows 11 標準作法（兩種方式）：**

### 方式 A：從開始功能表（最簡單）

1. 按鍵盤 `Windows` 鍵或點選工作列的「開始」圖示
2. 輸入 `powershell`
3. 在搜尋結果的 **「Windows PowerShell」** 上按 **滑鼠右鍵**
4. 選擇 **「以系統管理員身分執行」**
5. 出現 UAC（使用者帳戶控制）視窗時，點 **「是」**

### 方式 B：從快速選單（最快）

1. 按鍵盤 `Win + X`（或在工作列按右鍵 → 「終端機管理員」）
2. 選 **「終端機管理員）」** 或 **「Windows PowerShell (系統管理員)」**
3. 出現 UAC 視窗時，點 **「是」**

> **確認已開啟管理員權限**：在 PowerShell 視窗標題列應該看到
> 「系統管理員: Windows PowerShell」字樣。

---

## 2. 一次匯入所有排程（推薦）

在「系統管理員: Windows PowerShell」中，**複製貼上**以下整段指令：

```powershell
# 1. 獎學金監控爬蟲（每天 6 小時一次）
schtasks /create /tn "scholarship-monitor-crawl" /xml "C:\Projects\scholarship-monitor\scholarship-monitor\scholarship-monitor-admin.xml" /f

# 2. IntelPusher 每日備份（每天凌晨 3 點）
schtasks /create /tn "IntelPusher_Backup" /xml "C:\Projects\intel-pusher\intelpusher-backup-admin.xml" /f

# 3. IntelPusher 聯準會新聞（週一至週五早上 9 點）
schtasks /create /tn "IntelPusher_FedDaily" /xml "C:\Projects\intel-pusher\intelpusher-feddaily-admin.xml" /f

# 4. IntelPusher 週報（週一早上 8 點，跑全部來源）
schtasks /create /tn "IntelPusher_Weekly" /xml "C:\Projects\intel-pusher\intelpusher-weekly-admin.xml" /f

Write-Output "全部排程匯入完成！"
```

**預期輸出**（每行都應顯示 SUCCESS）：

```
SUCCESS: The scheduled task "scholarship-monitor-crawl" has successfully been created.
SUCCESS: The scheduled task "IntelPusher_Backup" has successfully been created.
SUCCESS: The scheduled task "IntelPusher_FedDaily" has successfully been created.
SUCCESS: The scheduled task "IntelPusher_Weekly" has successfully been created.
全部排程匯入完成！
```

> ⚠️ 若出現 `Access is denied`，代表你忘記以管理員身分執行 PowerShell，請關閉重開。

---

## 3. 逐一匯入（選擇性）

如果你只想更新其中某個任務，可單獨執行：

| 任務 | 指令 |
|------|------|
| 獎學金爬蟲 | `schtasks /create /tn "scholarship-monitor-crawl" /xml "C:\Projects\scholarship-monitor\scholarship-monitor\scholarship-monitor-admin.xml" /f` |
| IntelPusher 備份 | `schtasks /create /tn "IntelPusher_Backup" /xml "C:\Projects\intel-pusher\intelpusher-backup-admin.xml" /f` |
| IntelPusher Fed | `schtasks /create /tn "IntelPusher_FedDaily" /xml "C:\Projects\intel-pusher\intelpusher-feddaily-admin.xml" /f` |
| IntelPusher 週報 | `schtasks /create /tn "IntelPusher_Weekly" /xml "C:\Projects\intel-pusher\intelpusher-weekly-admin.xml" /f` |

---

## 4. 驗證設定成功

匯入完成後，在**一般（非管理員）PowerShell** 也可以查詢：

```powershell
# 查看獎學金爬蟲的完整設定
schtasks /query /tn "scholarship-monitor-crawl" /fo LIST /v
```

**重要欄位檢查清單：**

```
Status:                               Ready               ← 已啟用
Logon Mode:                           Interactive only     ← 登入即可執行
Power Management:                     Stop On Battery Mode ← 改成 No Start On Battery 
Run As User:                          User                ← 以你身分執行
Repeat: Every:                        6 Hour(s), 0 Minute(s)  ← 每 6 小時重複
Schedule Type:                        Daily               ← 每天排程
```

> ⚠️ 第 4 行 `Power Management` 若仍顯示 `Stop On Battery Mode`，可能是 XML 未正確匯入，
> 請重新用管理員身分執行步驟 2。

進階驗證：查看 XML 中實際寫入的設定

```powershell
# 檢查 scholarship-monitor 的 StartWhenAvailable
schtasks /query /tn "scholarship-monitor-crawl" /xml | Select-String "StartWhenAvailable"

# 檢查 BootTrigger
schtasks /query /tn "scholarship-monitor-crawl" /xml | Select-String "BootTrigger"

# 檢查電池設定
schtasks /query /tn "scholarship-monitor-crawl" /xml | Select-String "DisallowStartIfOnBatteries"
schtasks /query /tn "scholarship-monitor-crawl" /xml | Select-String "StopIfGoingOnBatteries"
```

**看到以下內容代表正確：**

```
<StartWhenAvailable>true</StartWhenAvailable>
<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
<BootTrigger><Enabled>true</Enabled></BootTrigger>
```

---

## 5. 測試關機自動補跑

### 測試 Startup 腳本（不需重開機）

Startup 資料夾的 VBS 腳本在使用者登入時會自動執行。可以直接手動執行測試：

```powershell
# 手動執行 startup 腳本（測試是否正常）
cscript "C:\Users\User\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\crawler-catchup.vbs"
```

檢查爬蟲結果是否為**最新資料**（非 stale data）：

```powershell
# 看最近一次爬蟲記錄
Get-Content "C:\Projects\scholarship-monitor\scholarship-monitor\data\runs.json" | ConvertFrom-Json | Select-Object -Last 1 | ForEach-Object { $_.results }
```

### 完整測試流程（需要重開機）

```
Step 1: 排程匯入完成（步驟 2）
Step 2: 手動執行一次 startup 腳本（上面 cscript 指令）→ 確認爬蟲正常
Step 3: 重開機
Step 4: 登入後等待 1-2 分鐘（startup 腳本自動執行）
Step 5: 用下面指令確認爬蟲已自動執行：

    Get-Item "C:\Projects\scholarship-monitor\scholarship-monitor\data\runs.json"
    # 看 LastWriteTime 是否為開機後的時間
    Get-Content "C:\Projects\scholarship-monitor\scholarship-monitor\data\crawl.log" -Tail 10
    # 看日誌最後幾行確認執行時間

Step 6: 確認排程下次執行時間：

    schtasks /query /tn "scholarship-monitor-crawl" /fo LIST /v | Select-String "Next Run"
    # 應該顯示今日的 00:00 / 06:00 / 12:00 / 18:00 其中一個
```

---

## 6. 排程任務說明總表

| 任務名稱 | 排程 | 觸發方式 | Startup 補跑 | 說明 |
|----------|------|---------|-------------|------|
| `scholarship-monitor-crawl` | 每 6 小時<br>(00:00/06:00/12:00/18:00) | 每日 + 重複 + 開機 | ✅ VBS | 爬各國獎學金來源 |
| `IntelPusher_Backup` | 每日 03:00 | 每日 + 開機 | ✅ VBS | Git 備份 + 加密 |
| `IntelPusher_FedDaily` | 週一~五 09:00 | 每週平日 + 開機 | ✅ VBS | 聯準會新聞爬蟲 |
| `IntelPusher_Weekly` | 週一 08:00 | 每週一 + 開機 | ✅ VBS | 全部來源爬蟲 |
| Startup 腳本 | 每次登入 | 使用者登入 | — | 三個任務依序執行 |

### 修復前後對照

| 項目 | ❌ 修復前 | ✅ 修復後 |
|------|-----------|-----------|
| 關機錯過排程 | 永遠不執行 | 開機自動補跑（StartWhenAvailable + Startup VBS） |
| 拔電源／電池模式 | 停止或拒絕執行 | 電池上也可執行 |
| 排程間隔 | 每天一次且無法重複 | 每 6 小時一次 |
| 執行結果 | LastResult 錯誤碼 | 預期正常完成（exit code 0） |
| 使用者登出 | 任務被終止 | 繼續執行（背景無視窗） |

---

> **維護備註**：若日後要修改任何排程設定，只需重複以上步驟：
> 1. 修改對應的 XML 檔案
> 2. 用管理員 PowerShell 執行 `schtasks /create /tn "..." /xml "檔案路徑" /f`
> 3. 所有舊設定會被新 XML 完全取代
