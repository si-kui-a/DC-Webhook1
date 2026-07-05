# Meta User Feedback — intel-pusher

---

## [2026-07-05] Android 專屬治理條款於本專案一律 N/A

本專案技術棧是 Python（requests/BeautifulSoup/sqlite3/cryptography），不是
Kotlin/Android。既有治理框架（Meta_Dev_Knowledge.md 慣例格式沿用自
CampusApp/HabitTimeline 等 Android 專案）中任何 Android 專屬條款，在
intel-pusher 一律標記 N/A，原因單純是技術棧不同，不代表條款本身有問題：

- `gradlew` / `build.gradle.kts` / Gradle Migration 相關規則 → N/A（本專案無 Gradle）
- Kotlin/Compose、Room migration（PAT-04 in HabitTimeline 的欄位新增流程）→ N/A
- ZenTheme/GoldAccent 之類的設計系統 token → N/A
- `.cursorrules` / `.design_system.md` / `.claude_logic.md` 三件套（CampusApp 慣例）→
  N/A，本專案沒有這三個檔案，也不需要補

## [2026-07-05] Discord webhook 架構決策：CORE_IMMUTABLE

比照使用者於任務描述中的既有慣例（見任務指示原文），本專案的下列設計視為
CORE_IMMUTABLE，之後除非使用者明確核准，否則不應變更：
- `push_webhook.py` 的 Webhook-only 架構（不接 Discord Bot API，只用
  webhook 單向推播）
- `encrypt_backup.py` 的 Fernet 對稱加密邏輯（金鑰本地存放、不進版控）
- `.env` 不進版控的規則
- PAT-04（build_embed 必須帶 published_at，見 Meta_Dev_Knowledge.md）

## [2026-07-05] SSH deploy key 一鑰一倉庫，不能跨專案沿用

原本設想比照 scholarship-monitor 專案已解決過的 SSH 認證方式沿用同一把
`id_ed25519_backup`。實際查證後：GitHub deploy key 機制是「一把公鑰只能
綁定一個倉庫」，該把鑰匙的公鑰已經（假設）用在 scholarship-monitor，技術上
無法重複綁定到 DC-Webhook1 這個新倉庫。改為產生專屬新金鑰
`id_ed25519_intel_pusher`，並用 `~/.ssh/config` 的 Host 別名
（`github.com-intel-pusher`）隔離，避免 git 預設嘗試錯的金鑰。
**回饋給使用者**：未來任何新專案要接 GitHub 備份，都要重複「產生專屬新
deploy key」這個步驟，不能假設既有 key 可以直接沿用。

## [2026-07-05] 【重大】.env.example 意外含真實 Discord webhook 全文的處置決策

Phase 5 過程中發現 `.env.example`（原本應是純樣板、只放 `xxxxx` 佔位符的
檔案）在某個時間點被寫入了 4 組**真實、完整**的 Discord webhook URL（含
ID 與 token），並在 `backup.sh` 第一次真實試跑時被一併 `git add` + commit +
push 到 GitHub 私有倉庫 `DC-Webhook1`（commit `c303cee`）。使用者自行在
GitHub 網頁上發現並提交了清除內容的修正（commit `1c5dd7b`，訊息
"Clear webhook URLs in .env.example"）。

**根因未完全查清**：`.env` 與 `.env.example` 兩個檔案在同一秒（07:25）被
寫入相同內容，看起來像是某個外部同步/複製動作，而不是本次對話裡任何一次
明確的工具呼叫（我沒有在自己的操作紀錄裡找到會做這件事的指令）。但無論
根因為何，我在讓 `backup.sh` 執行 commit/push 之前沒有重新核對
`.env.example` 這個「模板」檔案是否仍然乾淨——它明明就在 `git add` 清單
裡——這是我這邊的驗證流程缺口，該對每一個會被 commit 的檔案在 push 前逐一
確認內容，而不是只重新核對使用者剛提到的那個檔案（`.env`）。

**使用者的處置決定**（已明確詢問並取得回覆，記錄供未來參考，不要在未來
的操作中重新假設或重新詢問，除非情況改變）：
- 倉庫確認為**私有（private）**。
- **不**輪替（regenerate）這 4 組 Discord webhook。
- **不**用 `git filter-repo`/BFG 改寫歷史移除 commit `c303cee` 裡的明碼。
- 也就是說：這 4 組 webhook 的真實內容目前仍永久存在於這個私有倉庫的
  git 歷史裡（`c303cee`），這是使用者知情後接受的風險，不是遺留的待辦。

**強制規則（給未來任何類似情境）**：
1. 任何腳本第一次真的執行 `git add` + `commit` + `push` 之前，對其 add
   清單中「應該只是模板/範例」的檔案（`.env.example`、`*.sample`、
   `*.template` 等），要重新讀一次目前內容，逐行確認沒有真實憑證，而不是
   信任檔名或先前的印象。
2. 若真的發生類似洩漏，先問使用者「倉庫公開與否」與「是否輪替憑證／改寫
   歷史」兩個問題並記錄答案，不要自己代為決定，也不要每次重複問（除非
   使用者主動說情況變了）。
