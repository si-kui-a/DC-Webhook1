# RUNBOOK — 執行流程與指令序列

適用範圍：本機開發驗證 → Discord Bot 串接 → Docker 上線 → 上線後維運。
非 Android 專案，不涉及 build.gradle.kts / Room DB；此文件為本專案（Node.js）對應版本。

---

## Phase 1：本機安裝

```bash
cd scholarship-monitor
npm install
```

驗證點（無輸出即代表成功，非零 exit code 視為 BLOCKED）：

```bash
node --check src/index.js && echo "SYNTAX_OK"
```

---

## Phase 2：單一來源手動測試（新增來源或懷疑網站改版時使用）

MOE（`src/crawler/sources/moe.js`）、DAAD（`src/crawler/sources/daad.js`）與東海大學
（`src/crawler/sources/thu.js`）的 selector 已實測驗證過，非首次上線的必要步驟；
但新增來源或懷疑既有來源改版時，可用同一套方式單獨測試：

MOE 現在橫跨「民間團體獎助學金」與「政府機關獎助學金」兩個分類（`CATEGORIES` 陣列，見
`moe.js` 開頭），`run()` 回傳的 `items` 是兩個分類加總後的結果。⚠️ 分頁邏輯（跟隨 GridView 的
postback 換頁）**未經真實驗證**——目前兩個分類都只有一頁、沒有渲染分頁列，找不到真的有多頁
資料的分類可以實測；若之後某分類真的累積到需要換頁，第一次觸發時務必人工核對筆數是否正確。

```bash
node -e "
const DaadCrawler = require('./src/crawler/sources/daad');
new DaadCrawler().run().then(r => console.log(JSON.stringify(r, null, 2)));
"
node -e "
const MoeCrawler = require('./src/crawler/sources/moe');
new MoeCrawler().run().then(r => console.log(JSON.stringify(r, null, 2)));
"
node -e "
const ThuCrawler = require('./src/crawler/sources/thu');
new ThuCrawler().run().then(r => console.log(JSON.stringify(r, null, 2)));
"
```

判讀結果（`run()` 回傳 `{source, name, status, items, error}`）：
- `status: 'ok'` 且 `items.length > 0` → selector 正確
- `status: 'empty'`（`items` 為空陣列）→ selector 未命中，可能是網站改版，需人工重新確認 DOM 結構
- `status: 'error'` → 檢查回傳的 `error` 欄位訊息，或 `work/error.log`

### GenericCrawler 保底邏輯的實測診斷（新增來源時的參考）

新增東海大學來源時，同一頁面另外跑過一次 `GenericCrawler` 做對照診斷：

```bash
node -e "
const { GenericCrawler } = require('./src/crawler/base');
new GenericCrawler('診斷', 'http://fsis.thu.edu.tw/wwwstud/frontend/Scholarship.php').run()
  .then(r => console.log('items:', r.items.length));
"
```

結果：`GenericCrawler` 抓到 165 筆，其中 18 筆（10.9%）是「課程相關查詢」「校內公車查詢」等
導覽選單雜訊，非獎學金資料；專屬 class（`ThuCrawler`）抓到的 154 筆全部正確。**結論：
`GenericCrawler` 堪用但不乾淨，若某個新來源的頁面結構清楚穩定，值得投入寫專屬 selector；
只有在頁面結構混亂、寫專屬 selector 成本過高時才建議退回用 `GenericCrawler`。**

---

## Phase 3：Discord Bot 設定（報告推播 + 互動指令，統一必要）

單一 Discord Bot（見 `src/discordBot.js`）同時負責報告推播與互動指令。
以下步驟不可略過——缺任一項都會讓 Bot 無法登入或無法讀到指令內容。

### 3.1 建立 Application + Bot，取得 Token

```bash
# 3.1.1 開啟 https://discord.com/developers/applications → New Application → 任意命名
# 3.1.2 左側 Bot 頁籤 → Reset Token → 複製，即為 DISCORD_BOT_TOKEN
# 3.1.3 同頁面「Privileged Gateway Intents」區塊，開啟 MESSAGE CONTENT INTENT
#       [WHY 必要] 不開啟此 Intent，msg.content 一律收到空字串，
#       discordBot.js 的所有指令 regex（/myprofile、/update、/help 等）都會判斷失敗。
```

### 3.2 邀請 Bot、取得你的使用者 ID

```bash
# 3.2.1 左側 OAuth2 → URL Generator：scope 勾 bot，Bot Permissions 至少勾 Send Messages
#       用產生的連結把 Bot 邀請進「任一你自己在的伺服器」
#       [WHY] 僅用於讓 Bot 與你的帳號建立可視關係；實際互動全程走私訊(DM)，
#       不需要任何頻道權限，也不會在該伺服器公開發言。
# 3.2.2 Discord App 內：使用者設定 → 進階 → 開啟「開發者模式」
# 3.2.3 右鍵點自己的頭像/使用者名稱 → 複製使用者 ID，即為 DISCORD_USER_ID
# 3.2.4 先手動私訊該 Bot 任意一句話，開啟 DM channel
#       [WHY 必要] Discord Bot 無法主動私訊「從未與其建立過 DM channel」的使用者，
#       這一步不可省略，否則排程觸發推播時會直接拋出例外（已被 try-catch 包住不崩潰，
#       但收不到任何通知）。
```

```bash
cp .env.example .env
# 編輯 .env，填入 DISCORD_BOT_TOKEN 與 DISCORD_USER_ID
```

推播測試：

```bash
node -e "
const bot = require('./src/discordBot');
bot.start();
bot.whenReady().then(() => bot.send('測試訊息：獎學金監測系統上線確認'));
"
```

驗證點：你的 Discord 私訊收到訊息 → PASS；未收到 → 檢查 `work/error.log`，並確認已完成 3.2.4。

### 3.3 寫入類指令的 Rate Limit（安全性，Token 外洩情境的防線）

`/update`、`/rule`、`/addsource`、`/removesource` 這四個會寫入/改變系統狀態的指令，
同一使用者每 60 秒最多 5 次（滑動視窗）。查詢類指令（`/myprofile`、`/rules`、`/sources`、`/help`）不受限制。

超過限制時 Bot 會回覆：

```
⏳ 操作過於頻繁，請稍後再試（寫入類指令每 60 秒最多 5 次）。
```

同時在 `work/activity.log` 寫入一筆 `discordBot:rateLimited` 記錄（含被拒絕的完整指令內容），供事後稽核。
限流計數存在記憶體內，process 重啟會重置（不需要、也不應該持久化——這是短期防濫用機制，不是帳務資料）。

---

## Phase 4：單次執行測試（正式排程前的最後確認）

```bash
npm run run:once
```

檢查資料是否正確寫入（`data/db.json` 是 JSON 陣列，非 SQLite）：

```bash
node -e "
const db = require('./src/db');
console.log(db.getRecordsSince(new Date(Date.now() - 24*3600*1000).toISOString()));
"
```

報告產生測試（不觸發實際推播，僅印出內容）：

```bash
node -e "console.log(require('./src/report/generateReport').daily())"
```

---

## Phase 4.5：互動式條件更新測試

`npm start`（非 `--once`）會啟動 `discordBot.js` 的 Gateway 監聽，**並進入 `scheduler.js` 的常駐排程模式**
（每 6 小時爬取一次、每日 09:00 日報、每週日 18:00 週報、啟動時視情況立即補跑一次）。測試方式：

⚠️ **重要（曾實際發生過的問題）**：`npm run run:once`（Phase 4）只會執行一次爬取後就
呼叫 `process.exit(0)` 結束 process（見 `src/index.js` 的 `isOnce` 分支），**不會**讓
Bot 持續監聽私訊。如果只跑過 `--once` 從未執行過持久化的 `npm start`，Discord 私訊裡
打任何指令都不會有回應（不是指令邏輯壞掉，是根本沒有存活的 process 在監聽
`messageCreate` 事件）——這在 2026-07-04 的「全指令失效」事件裡被實際證實為根因：
`work/activity.log` 裡每一筆 `index:crawl` 紀錄都帶 `"once":true`，代表從未真正執行過
持久模式。**健康檢查（懷疑指令沒反應時，第一步先確認）**：

```bash
# Windows：確認有 node.exe process 正在跑
tasklist /FI "IMAGENAME eq node.exe"
# 若沒有任何結果，代表 Bot 沒有在監聽，需要重新執行 npm start
```

```bash
npm start
```

保持該終端機視窗開著，另外**私訊**你的 Discord Bot：

```
/myprofile
/update gpa=3.8
/update residence_city=台北市
/myprofile
```

驗證點：
- `/myprofile` 回傳的內容需與 `config/profile.json` 一致
- `/update` 後立即再問 `/myprofile`，數值需已變更（無需重啟 process；`ruleEngine.js` 每次都現讀 JSON 檔，沒有 `require()` 快取問題）
- `/update` 成功後，`work/activity.log` 應出現一筆 `discordBot:update` 記錄，包含 `userId`/`field`/`oldValue`/`newValue`
- 連續發送超過 5 次 `/update`（60 秒內）：第 6 次起應收到「操作過於頻繁」回覆，且 `work/activity.log` 出現 `discordBot:rateLimited` 記錄

非白名單帳號測試（確認未授權訊息被擋）：

```bash
# 用另一個 Discord 帳號（非 .env 中 DISCORD_USER_ID 對應者）私訊該 Bot /myprofile
# 預期：Bot 完全不回應（沒有任何回覆），但 work/activity.log 會出現一筆
# discordBot:unauthorized 記錄（含來源 userId 與前 200 字訊息摘要，供事後稽核）
```

Guild 頻道測試（確認公開頻道訊息一律忽略）：

```bash
# 在 Bot 所在的伺服器頻道（非私訊）輸入 /myprofile
# 預期：Bot 不回應（discordBot.js 僅處理 ChannelType.DM，Guild 訊息直接 return）
```

---

## Phase 5：Docker 上線

`Dockerfile` 已從 `node:18-slim` + `python3/make/g++`（SQLite 原生模組編譯需求）
改成 `node:18-alpine`（純 JS 依賴不需要編譯任何原生模組）+ `npm ci`（用既有的
`package-lock.json`，比 `npm install` 更快也更可重現）。

```bash
docker compose up -d --build
docker compose logs -f --tail=50
```

驗證點：

```bash
docker compose ps
# STATUS 應為 "Up"，非 "Restarting"（後者代表 CMD 內程序崩潰，需查 docker compose logs）
```

⚠️ 這次 Dockerfile/docker-compose.yml 的清理**未經實際 `docker build` 驗證**——本機
Docker daemon 未啟動（`docker build` 直接回報連不上 daemon），只做過人工逐行檢查，
語法上沒有明顯問題，但沒有真的 build 成功過。首次照這份 RUNBOOK 走 Phase 5 時，
請務必實際跑一次上面的指令並確認 `docker compose ps` 顯示 "Up"。

---

## Phase 6：上線後維運指令

### 6.1 每日健康檢查（可手動或另設 cron 執行）

```bash
tail -20 work/error.log
tail -20 work/activity.log
```

### 6.2 查詢近期已通知的項目

```bash
node -e "
const db = require('./src/db');
const since = new Date(Date.now() - 7*24*3600*1000).toISOString();
console.log(db.getRecordsSince(since).sort((a,b) => b.score - a.score));
"
```

### 6.3 查詢來源健康狀態（判斷是否有來源需要人工檢查 selector）

```bash
node -e "
const runLog = require('./src/runLog');
const since = new Date(Date.now() - 24*3600*1000).toISOString();
console.log(JSON.stringify(runLog.getRunsSince(since), null, 2));
"
```

若某來源持續出現 `status: 'empty'`，代表已觸發過一次即時 Discord 改版告警
（`sendRedesignAlert`，見 `src/scheduler.js` 的 `checkSourceTransitions()`）——只在由 `ok`
轉為 `empty` 的那一刻通知一次，之後不會每次排程都重複通知，回到 Phase 2 重新確認 selector。

也可以直接看 `data/sources.json` 的 `score`/`lastChecked`/`disabledReason` 欄位（`src/sourceAI.js` 打的健康分數）。
`data/sources.json` 是純執行期狀態（gitignored，不進 git）；首次執行時若不存在，會自動從
`config/sources.seed.json`（靜態、進 git 的初始清單）seed 一份出來，之後只會被
`/addsource`/`/removesource`/健康分數更新這幾個操作修改。

### 6.4 手動觸發日報 / 週報（不等排程時間）

```bash
npm run report:daily
npm run report:weekly
```

### 6.5 資料備份（純 JSON 檔案，不是 SQLite）

需要備份的是設定與已累積的紀錄，不是資料庫檔案：

```bash
mkdir -p backup
cp config/profile.json config/rules.json config/sources.seed.json data/sources.json data/db.json data/runs.json backup/ 2>/dev/null
```

（`data/*.json` 是執行期產生的快取/歷史，遺失只會造成短暫的重複通知或補跑，不影響 `config/` 下的個人設定；
`config/profile.json`、`config/rules.json`、`config/sources.seed.json` 才是真正需要小心備份的檔案。
`data/sources.json` 若遺失，重啟時會自動從 `config/sources.seed.json` 重新 seed，但透過
`/addsource` 額外新增、尚未寫回 seed 檔的來源會遺失，備份時仍建議一併複製。）

### 6.5a GitHub 異地備份（本輪起固化為每輪治理流程的一部分）

除了上述 6.5 的本機檔案複製，程式碼與設定（`config/`、`src/`、`README.md`、
`docs/`——不含 `.env`、`data/*.json` 等已 gitignore 的執行期產物/敏感資訊）
另外透過 git push 到 GitHub 遠端 repo（`origin` =
`https://github.com/lilichen-F/scholarship-monitor-token.git`）做**異地備份**。

⚠️ **這個 remote 的用途純粹是備份，不是協作**（單一開發者、單一 Discord 使用者的
個人專案，沒有其他協作者會 pull/clone 這個 repo）。

**流程**（自本輪起固化進 `[WORKFLOW: THE GOVERNANCE LOOP]` 的 POST 步驟，
詳見 `Meta_Dev_Knowledge.md`）：每輪任務結束前，commit 完成後執行 `git push`，
並在任務報告的 GIT 段落新增 REMOTE 欄位回報結果（成功會顯示
`pushed to {remote_url}@{branch}`；失敗會顯示 `FAILED - {原因}`，但不會讓
整個任務被判定失敗——本地 commit 已經完成的部分永遠優先保留）。push 前必查
`.gitignore` 是否仍正確排除 `.env`/`data/*.json` 等檔案，避免備份動作意外把
機密資訊或執行期產物推上遠端。

### 6.6 新增第二階段來源（Erasmus+、Fulbright 等）

`src/crawler/sources/thu.js`（東海大學獎助學金查詢）是一個實際走過這個流程的例子：

```bash
# 1. 先用瀏覽器工具或直接 fetch 實測目標頁面的真實 DOM 結構，不要假設selector
# 2. 決定走專屬 class（結構清楚穩定時，精準度較高）或 GenericCrawler（結構混亂時的保底方案）
# 3. 複製既有樣板（單頁來源可參考 daad.js/thu.js，需要分頁/多分類可參考 moe.js），繼承 BaseCrawler，實作 parseList()
cp src/crawler/sources/daad.js src/crawler/sources/newsource.js
# 4. 編輯 newsource.js 後，於 src/crawler/index.js 的 createCrawler() 加一條網址判斷
# 5. 在 config/sources.seed.json 加入該來源的 {name, url, enabled} 項目
# （既有安裝需刪除 data/sources.json 讓它重新 seed，或直接用 /addsource 手動加入）
```

⚠️ 新增中文標題的來源前，先確認 `config/rules.json` 的權重是否涵蓋中文關鍵字——
現在已內建從 MOE+東海大學 169 筆真實資料歸納出的常見中文詞（獎學金/助學金/清寒/優秀等，
見 README「計分權重」段落），但新來源若有自己一套不同的慣用詞，仍可能需要用
`/rule 關鍵字 權重` 補上。

---

## Phase 7：Debt 追蹤（對應 README「部署前必須處理的債務」，以 README 為準）

| P | Item | 驗證指令 | 完成判定 |
|---|---|---|---|
| P2 | `rules.json` 的 phd/master 計分權重跟六項條件的「年級」硬性排除閘門矛盾 | Phase 6.2 查詢 `data/db.json` 的 `source` 分布 | 年級閘門已在計分前排除 PhD/Master 限定項目，這兩個權重形同虛設；不在目前範圍內調整，見 README「硬性排除規則（六項條件）」 |
| P3 | `exclude_keywords` 沒有額外科系排他句型（限工程/限商管等） | Phase 2，抽樣真實資料檢查 | 實測 332 筆找不到這類句型，故未擴充，屬刻意的範圍限制 |
| P3 | `rules.json` 中文關鍵字仍有殘留覆蓋缺口（純機構/廟宇專有名稱） | Phase 6.2 查詢 `data/db.json` 的 `source` 分布 | MOE+東海大學 169 筆（六項閘門前）通過門檻筆數 0→105；剩餘 45 筆是無通用字樣的專有名稱，純關鍵字比對本來就無法涵蓋，屬預期限制 |
| P2 | `GenericCrawler`（`/addsource` 動態來源）沒有專屬 selector | Phase 2「GenericCrawler 保底邏輯的實測診斷」 | 實測 10.9% 雜訊率，堪用但不乾淨，屬預期行為 |
| P2 | MOE 的分頁邏輯（postback 跟隨）未經真實驗證 | Phase 2 對 MOE 執行測試，觀察是否曾經真的跟過一次分頁 | 待某分類真的累積到多頁時，人工核對第一次觸發的結果 |
| P3 | 沒有日期擷取，`publishDate` 固定為 `null` | Phase 6.2 查詢結果 | 待實作日期擷取 |
| P3 | Dockerfile/docker-compose.yml 清理未經實際 `docker build` 驗證 | Phase 5 | 待本機或 CI 有可用的 Docker daemon 時實測 |
