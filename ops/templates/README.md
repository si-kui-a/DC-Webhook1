# 可複製任務控制層

這組模板把排程任務的執行控制與業務腳本分離，適用於 Python、PowerShell 與 Windows Task Scheduler。

## 導入步驟

1. 將 `ops/run_task.ps1` 與 `ops/healthcheck.ps1` 複製到專案的 `ops/`。
2. 複製 `task-manifest.example.json`，填入每個任務的腳本、參數與 timeout。
3. 複製 `source-health.example.json`，為每個外部來源設定 `healthy`、`degraded` 或 `disabled`。
4. 讓排程器呼叫 `run_task.ps1`，不要直接呼叫 Python 腳本。
5. 先以 dry-run 執行，再接正式 webhook 或外部服務。

## 設計規則

- 每次執行記錄 `run_id`、commit、exit code、stdout、stderr。
- 同一任務不可重疊執行。
- 核心流程與 AI／外部增強分開；增強失敗只能標記 `degraded`。
- `SUCCESS_CORE`、`SUCCESS_ENRICHED`、`DEGRADED`、`FAILED_CORE` 必須分開記錄。
- 不把 webhook、token、API key 寫入模板或版本庫。

## 最小驗收

- 關閉所有 AI 金鑰後，核心任務仍可完成。
- 重跑同一事件不會重複推播。
- 外部來源 404、timeout 或配額耗盡時，能被記錄並進入 cooldown。
