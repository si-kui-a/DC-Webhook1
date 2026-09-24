# 雲端排程（GitHub Actions）操作手冊

建立：2026-09-24｜狀態：已實作，待切換

取代本機 Windows `IntelPusher-*` 排程（ResumeBot 除外，常駐 Discord bot 無法在 Actions 跑）。
設計理由寫在 `scripts/cloud_scheduler.py` 與 `.github/workflows/scheduler.yml` 開頭註解，這裡只記操作。

## 架構

| 元件 | 角色 |
|---|---|
| Google Apps Script `ops/gas_hourly_trigger.gs` | 每小時呼叫 workflow_dispatch（主要觸發） |
| `scheduler.yml` 的 `schedule` | 每天 4 次備援觸發（GitHub cron 本身會延遲數小時） |
| `scripts/cloud_scheduler.py` | 依台灣時間決定該跑哪些排程；當天補跑；每天最多重試 3 次 |
| Actions cache `ip-state-*` | 保存 `data.db` + `work/`（只留最新 3 份） |
| Artifact `state-backup-*` | 每天 03 點備份一份，保留 7 天 |
| Draft release `state-seed` | 只在 cache 為空時用來初始化；找不到就拒絕執行（避免空 DB 重推全部舊資料） |
| Secret `DOTENV` | 整份 `.env` 內容 |

## 一次性設定

1. **GitHub token**：Settings → Developer settings → Fine-grained tokens → 只選 `DC-Webhook1`，
   Repository permissions 只開 **Actions: Read and write**。
2. **Apps Script**：script.google.com 新專案，貼上 `ops/gas_hourly_trigger.gs`；
   專案設定 → 指令碼屬性新增 `GITHUB_TOKEN`；在編輯器執行一次 `installTrigger`（會要求授權）。
3. **rss2json**：rss2json.com 註冊免費帳號，把 key 加進 `.env` 的 `RSS2JSON_API_KEY`
   （Substack 擋雲端 IP 時的代抓；不設的話約 10 次後被 429）。

## 切換步驟（依序，避免 data.db 分岔）

1. 停用本機排程（ResumeBot 以外）：`Disable-ScheduledTask -TaskName IntelPusher-<名稱>`
2. 打包狀態：`tar -czf state-seed.tar.gz data.db work/`
3. 上傳：`gh release create state-seed state-seed.tar.gz --draft --title state-seed --notes "initial state"`
4. 設定 secret：`gh secret set DOTENV < .env`（不要把內容印出來）
5. 手動觸發一次並勾 `mark_done_today`：把當天本機已跑過的排程記成完成，避免重複推播
6. 確認 run 成功、cache 出現 `ip-state-*` 之後，刪掉 `state-seed` release

## 還原到本機

停用 GAS trigger →（需要最新狀態時）從最新的 `state-backup-*` artifact 或 cache 取回 `data.db` →
`Enable-ScheduledTask` 重新啟用本機排程。

## 手動操作

- 立刻跑某個排程：Actions → scheduler → Run workflow → `only` 填排程名稱（名稱見 `cloud_scheduler.py` 的 `TASKS`）
- 只看會跑什麼：勾 `dry_run`
- 重新檢查各來源在雲端是否可達：Actions → probe-cloud-reachability → Run workflow

## 已知限制

- `rental_search` 沒有排進雲端（資料源連續逾時）；修好後加回 `TASKS`。
- DAAD、勞動部偶爾從雲端 IP 失敗，靠當天重試補上。
- CheckTriggers 從每 20 分鐘改成每小時（免費 2,000 分鐘額度）。
