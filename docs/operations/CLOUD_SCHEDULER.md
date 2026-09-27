# 雲端排程（GitHub Actions）操作手冊

建立：2026-09-24｜更新：2026-09-27（`installTrigger` 移到切換最後一步）

取代本機 Windows `IntelPusher-*` 排程（ResumeBot 除外，常駐 Discord bot 無法在 Actions 跑）。
設計理由寫在 `scripts/cloud_scheduler.py` 與 `.github/workflows/scheduler.yml` 開頭註解，這裡只記操作。

## 架構

| 元件 | 角色 |
|---|---|
| Google Apps Script `ops/gas_hourly_trigger.gs` | 每小時呼叫 workflow_dispatch（主要觸發） |
| `scheduler.yml` 的 `schedule` | 每天 4 次備援觸發（GitHub cron 本身會延遲數小時） |
| `scripts/cloud_scheduler.py` | 依台灣時間決定該跑哪些排程；當天補跑；每天最多重試 3 次 |
| Actions cache `ip-state-*` | 只存**加密後**的 `state.enc`（`data.db` + `work/`），只留最新 3 份 |
| Artifact `state-backup-*` | 每天 03 點備份一份加密的 `state.enc`，保留 7 天 |
| Release `state-seed`（加密的 `state-seed.enc`） | 只在 cache 為空時用來初始化；找不到就拒絕執行（避免空 DB 重推全部舊資料） |
| Secret `DOTENV` | 整份 `.env` 內容，含 `STATE_KEY`、`HC_PING_URL` |
| Healthchecks.io | 每次 tick 回報成功／失敗；太久沒回報就通知 |

## 公開 repo 的防護（2026-09-26 改公開時加上）

| 風險 | 防護 |
|---|---|
| fork 發的 PR 可以讀主分支的 cache | cache 只存 `openssl` AES-256 加密的 `state.enc`，金鑰 `STATE_KEY` 在 `DOTENV` 裡 |
| 任何人都能看 Actions log | 任務輸出寫進 `work/logs/<任務>-<日期>.log`（隨加密狀態保存 7 天）；log 只印名稱、exit code、耗時 |
| 多行 secret 的遮蔽不保證逐行生效 | 寫出 `.env` 後逐一 `::add-mask::` 每個值 |
| 任何登入的 GitHub 使用者都能下載 artifact | 每日備份只上傳加密檔 |
| 外人 PR 觸發 workflow | Settings → Actions → 外部貢獻者的 PR 一律需要核准 |
| 歷史中曾外洩的 webhook（07-05 `.env.example`） | 公開前已刪除並換新 |

## 一次性設定

1. **GitHub token**：Fine-grained token，只選 `DC-Webhook1`，Repository permissions 只開 **Actions: Read and write**。
2. **Apps Script**：script.google.com 新專案，貼上 `ops/gas_hourly_trigger.gs`；
   專案設定 → 指令碼屬性新增 `GITHUB_TOKEN`。**先不要跑 `installTrigger`**，等切換步驟 7。
3. **`.env` 新增兩行**：`STATE_KEY=<隨機字串>`（`openssl rand -base64 32`）、`HC_PING_URL=<Healthchecks 的 ping 網址>`。
   rss2json 不需要帳號：Substack 摘要分散在 19／20／21 點，每次最多打 5 次。

## 切換步驟（依序，避免 data.db 分岔）

1. 停用本機排程（ResumeBot 以外）：`Disable-ScheduledTask -TaskName IntelPusher-<名稱>`
2. 打包並加密狀態（金鑰取自本機 `.env` 的 `STATE_KEY`，不要印出來）：
   `tar --force-local -czf - data.db work/ | STATE_KEY=<取自 .env> openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY -out state-seed.enc`
3. 上傳：`gh release create state-seed state-seed.enc --prerelease --title state-seed --notes "encrypted initial state"`
   （**不能用 draft**：workflow 的 token 只有讀取權限，看不到 draft release。公開 repo 的 release 是公開的，所以一定要加密）
4. 設定 secret：`gh secret set DOTENV < .env`（不要把內容印出來）
5. 手動觸發一次並勾 `mark_done_today`：把當天本機已跑過的排程記成完成，避免重複推播
6. 確認 run 成功、cache 出現 `ip-state-*` 之後，刪掉 `state-seed` release
7. 在 Apps Script 編輯器執行一次 `installTrigger`（會要求授權；建立觸發器後立刻 `tick()` 一次驗證 token）。
   不能提前跑：還沒有 `state-seed` 時每小時的 run 都會失敗並觸發 Healthchecks 警報；本機排程還開著時，兩邊同時跑會讓 data.db 分岔、重複推播

## 還原到本機

停用 GAS trigger →（需要最新狀態時）下載最新的 `state-backup-*` artifact，解密後覆蓋本機：

```
STATE_KEY=$(<取自本機 .env>) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:STATE_KEY -in state.enc | tar -xzf -
```

→ `Enable-ScheduledTask` 重新啟用本機排程。

## 手動操作

- 立刻跑某個排程：Actions → scheduler → Run workflow → `only` 填排程名稱（名稱見 `cloud_scheduler.py` 的 `TASKS`）
- 只看會跑什麼：勾 `dry_run`
- 看某次任務的完整輸出：下載 artifact 或 cache 的 `state.enc`，解密後看 `work/logs/`
- 重新檢查各來源在雲端是否可達：Actions → probe-cloud-reachability → Run workflow

## 已知限制

- `rental_search` 沒有排進雲端（資料源連續逾時）；修好後加回 `TASKS`。
- DAAD、勞動部偶爾從雲端 IP 失敗，靠當天重試補上。
- CheckTriggers 每小時一次（本機原本每 20 分鐘）。
