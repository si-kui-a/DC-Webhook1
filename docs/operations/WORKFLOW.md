# DC-Webhook1 長效低維護工作流程

## 0. 狀態盤點

開始前確認 branch、HEAD、upstream、工作樹、正式部署 commit、排程 Action、開放 PR 與已知故障。工作樹不乾淨時先停止。

## 1. 單一目標

每輪只處理一個主要目標：故障、測試、格式或文件。其他事項進 backlog，不在同一輪順手修改。

## 2. 最小變更

先建立回復分支，再以小 PR 修改。不得直接整合分歧的 `main`；正式分支只 cherry-pick 已驗證的必要 commit。

## 3. 驗證閘門

依序執行：離線 sample → dry-run → 單一正式任務 → 觀察日誌。測試觸發要有 event_id 去重，不能把全量同時啟動當成穩定性測試。

## 4. 結果狀態

每次執行記錄 `run_id`、commit、stdout、stderr、exit code 與業務狀態：`SUCCESS_CORE`、`SUCCESS_ENRICHED`、`DEGRADED`、`FAILED_CORE`。

AI 是可選增強；AI 失敗只能進入 `DEGRADED`，不得阻止核心抓取、計算、去重與固定格式推播。

## 5. 失敗處理

核心錯誤立即告警；外部來源連續失敗進 cooldown 或停用；長駐 Bot 使用 watchdog／Service；備份須驗證遠端 SHA 並實際還原。

## 6. 完成定義

`Draft`、`Tested`、`Merged`、`Deployed`、`Observed` 分開標記。沒有證據的項目不得標記完成。
