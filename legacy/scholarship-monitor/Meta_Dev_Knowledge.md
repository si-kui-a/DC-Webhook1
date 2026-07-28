# Meta Dev Knowledge

本檔案記錄跨輪次持續生效的治理流程規則（非單一任務的資料/程式碼決策——那些記錄在
`README.md`/`docs/RUNBOOK.md`）。專案內先前沒有 `CLAUDE.md` 或等效的專案層級指令檔案，
本檔案是這類規則的實體落地位置。

**本專案絕對路徑**（2026-07-05 起明確固定，避免與其他專案混淆）：
- `C:\Projects\scholarship-monitor\scholarship-monitor\Meta_Dev_Knowledge.md`（本檔案）
- `C:\Projects\scholarship-monitor\scholarship-monitor\Meta_User_Feedback.md`

⚠️ **不是** `C:\Projects\Meta_Dev_Knowledge.md`／`C:\Projects\Meta_User_Feedback.md`——
那兩份屬於使用者另一個 Android 專案（HabitTimeline），內容完全無關（Room DB、Compose、
AlarmManager 等）。使用者的 userPreferences 治理範本原本是為 HabitTimeline 設計，
範本裡若又出現指向 `C:\Projects\` 根目錄的 Meta 檔案路徑，是範本本身沿用了舊路徑設定，
需要主動核對並修正到本專案內部路徑，不得照單全收（2026-07-05 已核實：全域檔案內容
搜尋「scholarship/獎學金/discordBot/exclude_terms/ruleEngine」等本專案關鍵字皆 0 筆
命中，過去各輪次寫入動作都正確落在本專案內部路徑，未曾污染 HabitTimeline 知識庫）。

## 為什麼建立這份檔案（而非先前「不擅自建立」的判斷）

先前任務中曾有一輪要求「四欄位格式」相關的 debug 資訊被要求記錄在
`Meta_Dev_Knowledge.md`，當時回報此檔案不存在、且選擇不擅自建立——因為那是**任務指令
臨時假設的資料欄位**（假設 crawler 已經擷取某些內容，SCAN 後發現假設不成立），
性質上屬於單次任務範圍內的落差，不是需要跨輪次持續遵循的流程規則。

這次不同：使用者在對話中親口確認「已完成 GitHub 連動」並明確要求把 push 流程**固化進
治理範本的 POST 步驟**，這是使用者主動要求的**永久性流程變更**——不是任務指令裡的假設，
而是明確的治理規則異動，往後每一輪任務都要遵循。兩者性質不同，因此這次建立此檔案是
正當的。

---

## 治理範本異動記錄

### 2026-07-04：POST 步驟新增 REMOTE 欄位（本次生效，往後每輪皆適用）

`[WORKFLOW: THE GOVERNANCE LOOP]` 的 POST 步驟，原本只有：

```
POST:
- GIT: Output git status + hash.
- LOGS: Separate work/error.log (Errors) & work/activity.log (Task History).
```

**自本輪起**，POST 步驟固定新增 REMOTE 一項，完整版本如下：

```
POST:
- GIT: Output git status + hash.
- REMOTE: git push，並回報 push 結果
  （成功："REMOTE: pushed to {remote_url}@{branch}"；
  失敗："REMOTE: FAILED - {原因}"，push 失敗不視為任務整體失敗，
  但須顯著標註，本地 commit 永遠優先完成，不因 push 失敗而阻塞）
- LOGS: Separate work/error.log (Errors) & work/activity.log (Task History).
- SUM: Brief summary of changes.
```

**執行細節（每輪任務結束前必做，不得省略）**：
1. push 前必查 `.gitignore`，確認 `.env`、`data/*.json`（執行期產物）等敏感/易變檔案
   仍正確排除（沿用既有慣例，不是本次新增，但 push 前檢查這件事本身現在是強制的）。
2. 驗證遠端狀態不能只看 `git remote -v`（只證明有設定過 remote，不能證明真的 push
   成功過）——必須實際 `git fetch` 後 `git log origin/{branch} -1` 或等效指令，
   確認遠端上真的有對應 commit。
3. `git push` 失敗時記錄完整錯誤訊息，不得猜測原因；標記 REMOTE: FAILED，但本地 commit
   已完成的部分仍視為任務完成，不因 push 失敗回滾或阻塞其餘 POST/SYNC 步驟。

**同步影響**：`[CMD: 現況報告]` 產出的 `/reports/Status_{YYYYMMDD}.md` 範本，
第 4 節「GIT (Latest Commit)」自本輪起新增 REMOTE 欄位，格式比照上述 POST 步驟。

**目前遠端狀態（本輪驗證，僅供參考，會隨後續 push 變動，不是靜態事實）**：
`origin` = `https://github.com/lilichen-F/scholarship-monitor-token.git`，
`master` 分支已確認過往確實成功 push 過（非空 repo，本輪驗證時遠端 HEAD 為
`2e635d8`），性質為**異地備份**，非協作用途（單一開發者/單一 Discord 使用者專案）。

---

## 可複用的實作模式：「Discord 對話式管理設定檔」

這個模式在本專案裡已經獨立套用了三次（`config/rules.json` 的 `/rule`、
`config/sources.seed.json`/`data/sources.json` 的 `/addsource`/`/removesource`、
`config/exclude_terms.json` 的 `/排除關鍵字新增`/`/排除關鍵字移除`），值得記錄成
一條可複用原則，供未來任何新設定檔需要對話式管理時直接套用，不用每次重新設計：

1. **白名單可寫欄位/路徑**：不允許使用者透過指令建立全新的設定結構（分類/欄位），
   只能改動已存在的鍵值——新增結構是程式碼層級的決定（要不要接上對應的判斷邏輯），
   不該透過 Discord 指令做。找不到對應路徑時回覆明確錯誤，不靜默失敗。
2. **原子寫入**：一律用「先寫 `.tmp` 暫存檔，`fs.renameSync` 覆蓋正式檔」，不直接
   `fs.writeFileSync` 蓋掉正式檔——避免非同步寫入交錯或程式中途崩潰時讓設定檔停在
   「寫到一半」的損毀狀態。
3. **不快取、現讀現解析**：讀取端（`ruleEngine.js` 的 `loadRules()`/`loadExcludeTerms()`
   等）一律用 `fs.readFileSync` 每次重新讀取，不用 `require()`（會被 Node.js 模組快取）。
   否則 Discord 指令寫入後，下一次判斷邏輯讀到的還是舊資料，等於改了沒生效。
4. **rate limit + 稽核 log**：寫入類指令一律加進 `WRITE_COMMAND_PATTERNS`（沿用同一組
   滑動視窗 rate limit），並用 `logger.activity()` 記錄操作者/時間/變更內容——跟查詢類
   指令（唯讀）明確分流，不要讓兩者混在一起判斷。
5. **提供「查看目前有哪些可用選項」的查詢指令**，且直接複用同一份底層資料（不要另外
   寫死一份清單），避免使用者只能盲猜可用值——這是 2026-07-04 那輪任務的直接教訓
   （`/查詢獎學金`/`/篩選獎學金` 一開始沒有對應的選項查詢指令，使用者只能盲猜關鍵字/
   類別，後來才補上 `/獎學金關鍵字`/`/獎學金類別`）。

---

## AI 使用限制的解除範圍（2026-07-05，Universal Principle）

本專案從第一輪起就是「不得引入外部 AI API、不得引入本地常駐 AI 模型」的硬性限制。
使用者於 2026-07-05 明確授權針對單一功能（來源自動發現的合法性判斷）解除限制，
過程中確立以下原則，往後任何涉及 AI 使用範圍調整的任務都需遵守：

1. **AI 僅用於輔助發現/建議，不用於核心篩選判斷**——`ruleEngine.js` 六項硬性排除閘門、
   計分邏輯（`rules.json`/`exclude_terms.json`）不因這次解禁而改動，AI 判斷結果也不會
   自動寫入任何設定檔，最終仍需使用者透過既有指令（例如 `/addsource`）二次確認。
2. **所有 AI 呼叫必須有規則式降級路徑**：API 額度用盡/呼叫失敗時，退回純規則式驗證
   （見 `src/crawler/sourceValidator.js`），不得讓既有 4 個來源的排程監測受影響。
3. **範圍擴大需使用者逐次明確授權，不得自行推廣至其他模組**——這次解禁只涵蓋「候選
   來源合法性判斷」，明確排除了同一輪任務裡使用者主動提及、但要求不要順手處理的
   `detailParser.js`「未提供」欄位問題（那需要另一次獨立授權）。
4. **「使用者聲稱已完成前置設定」不能取代實際查證**：2026-07-05 當輪，任務描述宣稱
   `GEMINI_API_KEY` 已寫入 `.env`，但直接查證後發現並不存在；即使使用者稍後明確授權
   解除限制，仍需要實際讀取 `.env` 確認金鑰存在，並跑最小可行的 API 呼叫測試（`key`
   存在 ≠ `key` 有效 ≠ `key` 有配額可用，三個層次要分別驗證——本次金鑰通過
   `ListModels` 驗證但 `generateContent` 回報 429 quota exceeded，證實三者確實會
   在不同層次個別卡關）。

**目前狀態（2026-07-05 第二次複查，仍卡關）**：AI 輔助來源發現功能**尚未實作**。
使用者回報「已建立 Google Cloud 專案」後重新驗證，結果：
- **`.env` 裡的 `GEMINI_API_KEY` 前綴與長度跟上一輪記錄的完全相同**（`AQ.Ab8RN...`，
  53 字元）——研判使用者只建立了 Google Cloud 專案，但**尚未把 API key 換成跟該專案
  綁定的新 key**（或即使 key 沒變，專案/帳單設定本身也還沒接上）。
- `ListModels` 呼叫仍然成功（身份驗證持續有效，key 本身沒壞）。
- `generateContent` 呼叫仍然回報 HTTP 429，完整原文：
  `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`/
  `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`/
  `GenerateContentInputTokensPerModelPerMinute-FreeTier`，皆為 `limit: 0`。
  **關鍵線索**：quotaId 明確帶有 `-FreeTier` 字樣，代表這個 key 目前仍被歸類在
  免費方案，而且這個免費方案本身對 `gemini-2.0-flash` 的額度是 0（不是「用完了」，
  是從一開始就是 0）。

**給使用者的具體下一步**（不是「再試一次」，是需要實際操作的檢查清單）：
1. 前往 https://aistudio.google.com/apikey 確認目前使用的 key 是否真的關聯到新建立
   的 Google Cloud 專案（畫面上會顯示 key 對應的專案名稱）——如果 `.env` 裡還是舊 key，
   需要在這裡重新產生一把新 key 並更新 `.env`。
2. 前往 Google Cloud Console 該專案的「Billing」頁面，確認已連結有效的計費帳戶
   （建立專案本身不會自動連結計費帳戶，這是常見的漏掉步驟）。
3. 前往 Google Cloud Console 的「API 與服務」→「已啟用的 API」，確認
   「Generative Language API」已啟用於該專案。
4. 前往 https://ai.dev/rate-limit 或 Google Cloud Console 的「配額」頁面，直接查看
   `gemini-2.0-flash` 的 `generate_content_free_tier_requests` 額度是否仍顯示 0——
   免費方案在某些帳戶類型/地區可能仍然是 0，需要的話可能要啟用計費才會有非 0 額度
   （即使是低用量、可能落在免費額度內，Google 有時仍要求先綁定計費方式才會核發
   非零配額）。

**教訓（供未來其他專案申請 Gemini key 時參考）**：「建立 Google Cloud 專案」跟
「API key 綁定到該專案並取得實際可用額度」是兩個獨立步驟，前者不會自動導致後者；
且比對 key 的前綴/長度是否改變，是快速判斷「使用者是否真的換了新 key」的低成本
方法，值得每次都先做。

---

## 2026-07-05 現況盤點發現：指令輸入驗證的系統性落差（silent-fail pattern）

**發現過程**：上上輪任務修復了 `/獎學金說明` 對裝飾符號（`[11]`/`11.`/`11,`）的
容錯解析＋明確錯誤訊息，但這次全指令盤點時用 `handleMessage()` 逐一實測發現，
這個修法**只套用在 `/獎學金說明` 一個指令上**，其餘幾乎所有指令對「格式不完整/
不符合 regex」的輸入都是**完全無回應**（regex 比對失敗直接落到 `handleMessage()`
結尾，沒有任何 fallback 錯誤處理）。實測確認以下輸入皆無回應：
`/rule 獎學金 三`（分數非數字）、`/rule 獎學金`（缺分數）、
`/addsource 名稱`（缺網址）、`/addsource 名稱 ftp://x`（非 http(s)）、
`/update gpa`（缺等號值）、`/驗證來源 不是網址`、
`/排除關鍵字新增 school_restricted name_suffix`（缺語言/詞彙）、
`/查詢獎學金`（缺關鍵字）、`/篩選獎學金`（缺類別）、`/獎學金說明`（缺編號）。

**根因**：`handleMessage()` 目前是一連串 `if (regex.test(content)) {...; return;}`，
沒有「都比對不到時的 catch-all 分支」；只有 `/獎學金說明` 因為上輪任務指定要修這個
特定 bug，才額外加了「regex 放寬 + 事後驗證 + 明確錯誤訊息」的模式，其他指令從
最初就沿用「只在完全符合 regex 時才處理」的寫法，從未系統性補上錯誤提示。

**教訓（Universal Principle，未來任何 Discord/CLI 指令解析都適用）**：
「輸入驗證要嘛在最外層做 catch-all 錯誤提示，要嘛每個指令都各自補上失敗分支」，
不能只修「使用者這次剛好回報的那一個指令」就當作已經解決同類問題——這類 bug
通常是設計模式層級的（regex 比對失敗=靜默忽略），不是單一指令的個案，下次再有
使用者回報「指令沒反應」時，應該優先懷疑是不是同一個系統性成因，而不是每次都
當成全新的獨立 bug 修。**本輪盤點時僅記錄不修復**（當時任務明確要求現況盤點、
不修改程式碼）。

**2026-07-05 後續已修復**：新增 `matchOrUsageError(commandName)` helper
（`src/discordBot.js`），回傳一個比對函式：先用「指令名稱 + 空白，或訊息恰好
等於指令名稱」判斷這則訊息「看起來是不是要打這個指令」（`intended`），避免
`/rule` 誤判成 `/rules` 的前綴衝突；若 `intended` 但完整 regex 比對不到，代表
格式有問題，回傳 `{intended: true, match: null}` 讓呼叫端回覆明確用法提示。
統一套用到全部 12 個帶參數指令：`/update`、`/rule`、`/排除關鍵字新增`、
`/排除關鍵字移除`、`/驗證來源`、`/擷取引用連結`、`/addsource`、`/removesource`、
`/查詢獎學金`、`/篩選獎學金`、`/依地區查詢`、`/獎學金說明`。**未來任何新增的
帶參數指令，都應該用這個 helper 包裝，不要再用裸的 `if (regex.test(content))`**，
否則會重蹈同一個 silent-fail 覆轍。

**技術債附帶修復**：同一次修復也解決了 `/篩選獎學金` 的 `\S+` regex 無法比對
含空白類別名稱（例如 `European Funding Guide（歐洲）`）的既有 bug（改用 `.+`，
沿用 `/擷取引用連結` 已驗證過的修法）。

---

## 2026-07-05：「引用連結擷取」與「AI 自動判斷收錄」的邊界區分（Universal Principle）

使用者持續用類似措辭要求「爬蟲應可自動延伸擴充全世界公開獎學金網站」。這句話
每次出現都需要重新判斷落在邊界的哪一側，因為字面上容易被理解成「系統自主探索」，
但實際上有兩種完全不同性質的實作，判斷依據如下：

| 判斷面向 | ✅ 允許（不是語意判斷） | ❌ 不允許（等同 AI，違反硬性限制） |
|---|---|---|
| 動作性質 | 從已知頁面**抽取**已經存在的連結（DOM/正則） | **判斷**一個連結「是不是獎學金網站」 |
| 資料來源 | 只處理使用者已經核准過的既有來源頁面內容 | 憑空搜尋/爬取整個網路找「可能相關」的網站 |
| 最終決定權 | 100% 在使用者（`/驗證來源` + `/addsource` 兩道人工確認） | 系統自動判斷後就收錄，不需要人工二次確認 |
| 本專案的實作對應 | `src/crawler/linkExtractor.js`（`/擷取引用連結`） | `src/sourceDiscoveryAI.js`（Gemini 輔助判斷，目前卡在額度問題，見上方章節） |

**判斷口訣**：如果這個功能的輸出是「一份候選清單，使用者還要再做點什麼才會真的
生效」，屬於允許範圍；如果輸出是「系統自己就做了最終判斷/收錄」，屬於 AI 範圍，
需要使用者逐次明確授權（比照 Gemini 額度問題章節記錄的既有授權流程）。

**本輪也發現一個連結擷取本身的真實 bug（供未來同類功能參考）**：抽取候選外部
連結時，若只比對「完全相同的 hostname」會漏抓同一機構的其他子網域（例如
`fsis.thu.edu.tw` 與 `www.thu.edu.tw`），把明顯的機構內部導覽連結誤判成「候選
外部連結」。改用「根網域」比對（處理 `.edu.tw`/`.com.tw` 等複合 TLD 的情況）才
正確排除。這類「同機構跨子網域」的雜訊，跟 `GenericCrawler` 的 `NAV_NOISE_WORDS`
文字黑名單是同一個問題的兩種不同偵測角度（一個看連結文字，一個看網域），未來
若要再強化雜訊過濾，兩種角度都要考慮，不能只做其中一種。

---

## 2026-07-05：全專案結案盤點——發現 MOE 健康分數機制靜默失效（Universal Principle）

**發現過程**：使用者要求逐項實測驗證所有先前輪次宣稱完成的功能是否真的可視為
「結案」。逐一比對 `data/sources.json`（健康分數記錄）與 `data/runs.json`
（實際爬取紀錄）時，發現 MOE 的 `lastChecked` 停留在 2026-07-03，比同一批
（DAAD/THU/EFG，皆為 2026-07-04 16:00:07）舊了一整天，但 `runs.json` 明明
記錄 MOE 在那次爬取裡也成功抓到 15 筆。

**根因**：`sourceManager.updateSourceHealth(sourceUrl, ...)` 用**完全字串比對**
（`list.find(s => s.url === sourceUrl)`）尋找要更新的紀錄。`crawler/index.js`
呼叫這個函式時傳入的是**crawler 實例自己的 `this.url`**（建構子裡寫死的網址），
不是 `sources.json` 儲存的網址。`MoeCrawler` 的建構子鎖定 `Content_List.aspx?
n=D50A7AEB3C165858`（特定分類頁），但 `sources.seed.json`/`data/sources.json`
儲存的是 `Grants.aspx?n=2BBF7170197CE7D3&sms=...`（另一個頁面）——兩者不同，
`find()` 找不到對應紀錄，`updateSourceHealth()` 就靜默 `return`，MOE 的分數/
`lastChecked` 從此凍結在最後一次「剛好對得上」的狀態。`createCrawler()` 本身是
用網址**正則比對**（寬鬆）決定要 new 哪個 crawler class，不要求網址完全一致，
所以爬取本身完全正常，只有「回報健康分數」這一步用了完全比對，兩種比對邏輯
的嚴格程度不一致，才會出現「爬蟲正常運作、但監控機制默默壞掉」的落差。

**教訓（Universal Principle）**：「同一個資料實體（這裡是『來源網址』）在
不同模組之間流動時，若中間某個環節（這裡是 crawler 建構子）把它换成了另一個
語意相近但字面不同的值，後續任何用『完全比對』銜接的地方都會靜默斷裂」。
這類 bug 特別危險，因為它不會拋錯、不會被既有測試發現（除非測試會比對兩份
資料的時間戳记是否同步更新），系統看起來一切正常（爬蟲持續正確運作），只有
「監控機制本身」悄悄失靈。**未來新增/修改任何 crawler 時，若建構子裡的網址
跟 `sources.seed.json` 不一致，必須同步更新兩邊，或改用更穩定的識別鍵（例如
來源名稱）而非網址做比對**。本次僅記錄不修復（結案盤點任務是驗證性質），
記入 DEBT 供下次處理。

**本輪結案盤點總覽**（供未來任務快速查閱哪些已驗證、哪些有已知問題）：

| 功能 | 驗證方式 | 結果 |
|---|---|---|
| 六項硬性排除閘門 | 457 筆真實語料回歸測試 | ✅ 通過（MOE 6/DAAD 16/THU 85/EFG 33，與歷史基準一致） |
| 特定身份排除（原住民/僑生/難民/身心障礙） | 同上回歸測試 | ✅ 通過 |
| 多語言排除字典（zh/en/de） | 直接檢視 `config/exclude_terms.json` 結構 + 回歸測試裡 Promotion(de)/demonym(en) 真實命中 | ✅ 通過 |
| 4 個來源監測 | `data/runs.json` 最新紀錄確認 4 個來源皆 `status: ok` | ✅ 通過 |
| 來源健康分數機制 | 比對 `data/sources.json` 與 `data/runs.json` 時間戳 | ⚠️ **MOE 靜默失效**（見上方根因分析），DAAD/THU/EFG 正常 |
| 詳情頁四欄位擷取+分類分組+截止日過濾 | 手動觸發 `daily()`/`weekly()`，檢視實際輸出格式 | ✅ 通過 |
| Discord 對話式管理（排除關鍵字/來源） | `handleMessage()` 逐一實測 | ✅ 通過 |
| 引用連結擷取+依地區查詢 | `handleMessage()` 逐一實測 | ✅ 通過 |
| GitHub 異地備份 | `git fetch` 後比對 `origin/master` HEAD | ✅ 通過 |
| 日報/週報排程（node-cron） | 確認存活 process 的啟動 log 有註冊 3 個 cron job；手動觸發驗證內容格式 | ✅ 通過 |
| 8 小時補跑邏輯 | 用真實 `runLog.getLastSuccessAt()` 時間戳驗證判斷邏輯（距上次成功 1.44 小時，正確判斷不需補跑） | ✅ 通過 |
| 統一輸入格式錯誤處理 | `handleMessage()` 逐一測試 13 組先前無回應案例 | ✅ 通過（本輪修復） |

**結論**：專案**不能**視為完全結案——MOE 健康分數機制的靜默失效是一個真實、
會影響長期監控可靠性的問題（若 MOE 未來真的開始失敗，自動停用安全網不會
觸發），雖然不影響目前的爬取/顯示功能，但仍是一個「監控盲點」，記入 DEBT。

---

## 2026-07-05（續）：MOE 健康分數靜默失效——修復（Option B + C）

**SCAN（先確認根因範圍，不只修一處）**：重新讀取 `daad.js`/`thu.js`/`efg.js` 三個
既有正常運作來源的建構子，確認它們的硬編碼網址目前跟 `sources.seed.json` 完全一致
（沒有現行 bug），但架構本身——「crawler 建構子硬編碼一份網址、`sources.seed.json`
再分別維護一份」——跟 MOE 出事前的架構完全相同，屬於**同一類設計風險**，只是還沒
被觸發。若只修 MOE 一處（選項 A），未來任何人改 `sources.seed.json` 裡 DAAD/THU/EFG
的網址卻忘記同步改對應 crawler 建構子，會重演一模一樣的靜默失效。

**決定**：選擇**選項 B（single source of truth）+ 選項 C（防呆日誌）組合**，
明確排除單獨使用選項 A（最小修補）。理由：選項 B 從架構層面消除「兩處分別維護
同一個值」的可能性，一次性防止全部 4 個來源未來重演同類問題，不只是修 MOE 這一個
症狀；選項 C 作為選項 B 之外的第二層防護——即使未來又有人在某處繞過 single source
of truth（例如新增第 5 個來源時沒有遵循這個模式），至少比對失敗會被記錄下來，不會
再像這次一樣潛伏數月不被發現。兩者互補，不是二選一。

**實作**：
1. `src/crawler/index.js` 的 `createCrawler(source)` 改為把 `sources.seed.json`
   儲存的 `source.url` 傳入所有 4 個專屬 crawler 建構子（`new MoeCrawler(source.url)`、
   `new DaadCrawler(source.url)`、`new ThuCrawler(source.url)`、
   `new EfgCrawler(source.url)`）。
2. 4 個 crawler class（`moe.js`/`daad.js`/`thu.js`/`efg.js`）的建構子統一改成
   `constructor(url) { super(name, url || 舊硬編碼常數); }`——`url` 有傳入時優先使用，
   只有沒傳入時（例如單元測試直接 `new MoeCrawler()`）才 fallback 到舊常數，
   保留向後相容但不影響正式排程行為。
3. `src/sourceManager.js` 的 `updateSourceHealth()` 在 `list.find()` 找不到對應紀錄時
   新增 `logger.error("sourceManager:updateSourceHealth", new Error(...), { knownUrls })`，
   取代原本的靜默 `return`。

**架構差異備註**：THU 的 `this.url` 同時是標籤**也是**實際抓取目標（`BaseCrawler`
預設 `fetchRaw()` 直接 fetch `this.url`），改變它會真的改變抓取行為；DAAD/EFG/MOE
的 `this.url` 純粹是標籤（三者都覆寫了 `fetchRaw()`，用各自的獨立常數/邏輯抓取，
不讀 `this.url`）。這個差異不影響本次修復的正確性——因為傳入的 url 本來就等於
`sources.seed.json` 原本就在用的那個值，但值得記錄，避免未來誤以為 4 個 crawler
的 `this.url` 語意完全一樣。

**測試驗證（實測數值，非假設）**：
- MOE 修復前後對比：`data/sources.json` 的 MOE 記錄 `lastChecked` 從
  `2026-07-03T16:55:51.766Z`（凍結狀態）→ 重新觸發 `crawlAll()` 後變為
  `2026-07-04T17:41:03.739Z`，證實 `updateSourceHealth()` 這次真的找到對應紀錄並
  更新了。
- 回歸測試：再次執行 `crawlAll()`，`lastChecked` 全部同步從 `17:41:03` 前進到
  `20:40:57`（MOE/DAAD/THU/EFG 四個都更新），且 DAAD/THU/EFG 的 `score` 維持不變
  （皆為 10），證實本次修復對這三個既有正常來源沒有造成任何行為改變。
- 防呆機制驗證：直接呼叫
  `sourceManager.updateSourceHealth('https://this-url-does-not-exist.../mismatch-test', [])`，
  確認 `work/error.log` 新增一行
  `[sourceManager:updateSourceHealth] 找不到對應的來源紀錄，健康分數未更新：sourceUrl=...`，
  附帶 `knownUrls`（目前 4 個合法來源網址列表），證實防呆日誌確實生效。

**Universal Principle（供未來新增來源預設遵循）**：**網址等關鍵設定值不應該在
多個地方分別維護（crawler 程式碼 vs 設定檔）**。只要同一個概念性的值需要同時被
「執行邏輯」跟「設定檔」用到，就應該讓設定檔是唯一權威來源，執行邏輯在初始化時
從外部注入/讀取這個值，而不是各自寫死一份指望人工記得同步。新增第 5 個來源時，
新的 crawler class 建構子應該比照這個模式接受可注入的 url 參數，不要走回頭路
在建構子裡硬編碼網址常數（除非是提供給單元測試用的預設 fallback 值）。

---

## 2026-07-05（續）：排除邏輯四維度體檢——文件不適用澄清 + 真實缺陷修復

**背景**：使用者提供一份「排除關鍵字失效」技術分析文件，但該文件描述的系統跟本專案
技術棧完全不符（Python bot/`filters/scholarship_filter.py`、SQL 資料庫、Dart/Flutter/
Drift 前端、`/設定排除` 單詞指令格式 vs 本專案 Node.js/JSON 檔案儲存/`/排除關鍵字新增`
四參數指令格式）。本輪**未依該文件的具體程式碼建議修改任何檔案**，只借用其提出的
「AND/OR混淆、執行順序錯置、欄位比對盲區、字串標準化落差」四個病因分類作為體檢框架，
對本專案真實程式碼逐項實測。

**體檢結果**：

1. **AND/OR 邏輯**：`filter()` 的六項硬性排除閘門用連續 `if (isExcludedByX(...)) return false;`
   串接，屬 AND 語意（任一命中即排除，非 OR 誤判）。實測「限清寒學生申請 PhD 獎學金
   scholarship」（若計分應有 phd+獎學金+scholar=6 分，遠高於 threshold=2）搭配
   `income_status=normal`（不符清寒），結果**正確被排除**（存活筆數 0），證實高分不會
   蓋過硬性排除，無缺陷。

2. **執行順序**：`crawlAll()` 的呼叫順序是「抓取→比對 unseen→`ruleEngine.filter()`→
   `detailParser.enrichWithDetails()`」，`filter()` 永遠處理當次新抓到的 unseen 項目，
   不會處理舊資料。`loadRules()`/`loadProfile()`/`loadExcludeTerms()` 三者皆用
   `fs.readFileSync` 現讀現解析（非 `require()` 快取），實測修改 `exclude_terms.json`
   後立即呼叫 `loadExcludeTerms()` 能讀到最新內容，確認先前已修復的「快取沒重讀」問題
   未回歸，無缺陷。

3. **欄位比對盲區**：確認 `filter()` 的 `rawTitle = i.title` 只比對標題，`ruleEngine.filter()`
   在 `detailParser.enrichWithDetails()`（擷取身份要求/申請條件/申請辦法/截止日期四欄位）
   **之前**執行，代表排除規則永遠看不到這四個欄位。**此為已知的既有限制**（README.md
   「訊息四欄位格式」章節已記錄「以上規則只能比對公告標題...若限制條件只寫在內文裡，
   這幾條規則會漏放」），非本輪新發現的缺陷，僅重新確認現況與文件記錄一致。

4. **字串標準化**：**發現真實缺陷**（非文件假設情境，本輪實測發現）。`scoreItem()`/
   `filter()` 原本只用 `.toLowerCase()` 處理英文大小寫，全形英數字（例如
   「Ｇｅｒｍａｎｙ」）是跟半形「germany」不同的 Unicode 字元，`.toLowerCase()` 不會
   轉換全形→半形。實測：半形「Germany Scholarship Grant Program」正確算出 score=5，
   全形版本同一句話算出 0 分被排除——代表若標題用全形英文書寫（部分政府網站/特定輸入法
   來源常見），會被計分邏輯**完全忽略、靜默漏放**，跟 `isExcludedBySchool()` 先前的
   MOE bug 屬於同一類「比對邏輯對特定輸入格式完全失效但不會報錯」的風險模式。

   **修復**：新增 `normalizeTitle(title)` 函式，用 `String.normalize("NFKC")` 把全形
   英數字/標點正規化成半形 canonical 形式（實測確認中文字/CJK Unified Ideographs 不受
   NFKC 影響），在 `scoreItem()` 與 `filter()` 的 `survivors` 比對迴圈最先讀取 `title`
   的兩處套用，下游所有排除函式（`isExcludedBySchool`/`isExcludedByIncome`/
   `isExcludedByNationality`/`isExcludedBySpecialStatus`/`isExcludedByGrade`）都吃同一個
   已正規化的 `rawTitle`，不需要逐一修改每個函式內部。

   **回歸驗證**：真實 `data/db.json`（140 筆）分別用修復前（git HEAD 版本）與修復後的
   `ruleEngine.js` 各跑一次 `filter()`，通過筆數皆為 **135**，完全一致——因為目前
   140 筆真實資料裡 0 筆含全形字元，NFKC 正規化對它們是 no-op，只在（目前尚未真實出現的）
   全形字元情境下改變行為，這跟本專案先前「僅記錄未驗證詞彙」的處理原則（例如
   `upperclass_only`/`disability_illness` 部分詞彙）一致：修正已確認的邏輯缺陷，但誠實
   標注目前真實語料尚未觸發過。

**exclude_terms.json 分類疑慮（延續調查，非本輪修改）**：確認 `config/exclude_terms.json`
的 `school_restricted.name_suffix.zh` 目前仍是**未提交**的工作副本變更，內容從原本的
`["大學", "學院"]`（`isExcludedBySchool()` 用來動態組出「OO大學/OO學院」校名比對 regex
的通用尾綴詞）整個被取代成一組具體獎學金/科系專有名詞（`["原住民", "中國工程師學會-
莫衡先生紀念獎學金", "體育", "美術系", "國貿系", "新住民", ...]`），且內容持續在增加
（第一次發現時只有「原住民」一項，本輪重新檢視已變成 11 項）。**實測證實這是真實
regression，不只是分類錯置**：用修復前（git HEAD）版本測試「限國立臺灣大學在學學生
獎學金」（使用者 `school=東海大學`，非本人學校）**正確被排除**；用目前工作副本的
（缺少「大學」「學院」尾綴詞）版本測試同一標題，**未被排除、會被使用者看到**——代表
只要 `school_restricted.name_suffix.zh` 維持目前內容，`isExcludedBySchool()` 對**所有**
「限OO大學/OO學院在學學生」句型的排除規則完全失效，不只是「原住民」誤放這一項。至於
「原住民」本身雖然被誤放在 `school_restricted` 底下，但由於 `special_status.indigenous`
（獨立分類，未被本次變更觸及，仍是 `["原住民"]`）已經有自己的比對路徑
（`isExcludedBySpecialStatus()`），實測「限原住民學生獎助學金」在目前工作副本狀態下
**仍正確被排除**——代表「原住民」誤放本身目前無實質影響（冗余但無害），真正有實質影響
的是「大學」「學院」尾綴詞遺失。此為**未提交的工作副本狀態**，不屬於本輪 SCAN 範圍內
的程式碼修改，僅如實記錄供使用者決定是否/如何處理（保留現狀 vs 還原 vs 重新分類）。

---

## 2026-07-05（續）：school_restricted regression 修復——方案 C（分類歸位）

使用者選擇方案 C：保留通用尾綴詞「大學」「學院」（因為使用者自己是大學生，不希望系統把
泛稱詞本身當成排除訊號），並釐清「他校限制」（school_restricted）跟「特定科系限制」
（既有的 `exclude_keywords` 機制）是兩種不同性質的排除邏輯，不應混在同一個陣列裡。

**SCAN 逐一分類 11 項詞彙**（見 `README.md` DEBT 表「已修復」條目的完整表格）：實測用
`data/db.json` 140 筆真實記錄比對每個詞彙的命中標題，確認每一項的真實性質：
- 「原住民」→ 純粹重複（`special_status.indigenous` 本來就有，未受污染），刪除即可
- 「新住民」→ 跟原住民/僑生/難民同屬身份別限定，但**目前沒有對應分類**（既有
  `special_status` 只有 indigenous/overseas_student/refugee/disability_illness 四種）。
  比照既有模式（refugee/disability_illness 都是先前輪次用同一套「新增 profile 布林欄位 +
  special_status 新 key」的模式加入），新增 `special_status.new_resident` + 
  `profile.is_new_resident`，而非勉強塞進既有分類或丟進 `exclude_keywords`——身份別限定
  跟科系/學校限定是不同語意類別，比對邏輯（`isExcludedBySpecialStatus` vs `isExcludedBySchool`
  vs `exclude_keywords` 純 substring）也不同，混用會讓分類名不符實
- 「體育」「美術系」「國貿系」+ 從「會計系羅黃葉勤學敦品獎助學金」抽出的「會計系」→
  真實命中「東海大學體育獎學金」「美術系吳學讓國畫獎學金」「國貿系林財丁教授獎助學金」等
  具體系所限定獎學金，性質是科系限制，移至 `config/profile.json` 既有的 `exclude_keywords`
  （該檔案原本的 `_comment_major_aliases` 就已明確記載「既有的 exclude_keywords 已用
  『限理工/限醫學/限博士』這種反向排除清單間接處理科系不符的情況」，證實這就是使用者
  所說的「既有科系排除邏輯」，不是新機制）
- 「中國工程師學會-莫衡先生紀念獎學金」「全國台南一中校友總會-母校畢業校友現就讀各大學
  系所獎助學金」→ 完整獎學金名稱，不屬於學校/科系/身份別任何一類（前者是專業學會會員
  資格限定，後者是特定高中校友身份限定，皆非本輪授權建立新分類的範圍），移至
  `exclude_keywords` 做個案排除（現讀現解析、純 substring 比對，功能上等同於「看到這個
  完整標題就排除」，不需要為了這兩個孤例另外設計新分類）
- 「新應材新住民子女獎學金計畫」「新住民及其子女培力與獎助學金」「東海大學原住民學生
  優秀獎學金」→ 完整標題已被抽出的短詞（新住民/原住民）涵蓋，屬多餘重複，直接刪除
  不需另外保留（保留只會增加維護負擔，沒有額外涵蓋範圍）

**修復**：`school_restricted.name_suffix.zh` 恢復為 `["大學","學院"]`；新增
`special_status.new_resident.zh = ["新住民"]`（`exclude_terms.json` + `ruleEngine.js` 的
`DEFAULT_EXCLUDE_TERMS` + `isExcludedBySpecialStatus()` 三處同步）；`profile.json` 新增
`is_new_resident: false` + `exclude_keywords` 追加 6 個詞（體育/美術系/國貿系/會計系/
中國工程師學會-莫衡先生紀念獎學金/全國台南一中校友總會-母校畢業校友現就讀各大學系所
獎助學金）。

**測試驗證**（實際數值）：
- 「限國立臺灣大學在學學生獎學金」→ 排除（他校校名判斷恢復正常）
- 「大學部獎學金開放申請」→ 通過（**使用者最擔心的情境**：泛稱詞不會被誤判為排除訊號，
  因為 `isExcludedBySchool()` 要求「限」+校名+「(在學)學生」的完整句型，單純出現
  「大學」二字不構成任何一種句型比對，這個防護本來就寫在程式碼裡未被本次改動觸及）
- 「東海大學專屬獎學金」→ 通過（本校校名，`isSameSchool()` 正確判斷）
- 「限原住民學生獎助學金」→ 排除（移回 `special_status.indigenous` 後確認無回歸）
- 「美術系吳學讓國畫獎學金」→ 排除（科系限制歸位到 `exclude_keywords` 後開始正確生效）
- 「新住民及其子女培力與獎助學金」→ 排除（新增的 `special_status.new_resident` 生效）
- **真實 140 筆語料回歸測試**：THU 從 85→71（少 14 筆，逐筆核對：4 筆美術系+2 筆國貿系+
  2 筆體育+1 筆會計系+1 筆中國工程師學會+1 筆台南一中校友會+2 筆新住民+1 筆已存在的原住民
  類別去重不影響計數，共 14 筆，全數為本輪新增排除規則正確生效，無誤判）；MOE/DAAD/EFG
  三者維持 6/16/28 不變，無非預期行為改變

**Universal Principle**：**排除詞彙陣列的欄位性質需要先分類再新增，不可直接覆蓋通用詞**。
本次 regression 的根本問題不是「多加了幾個詞」，而是把一個**結構性、跨所有來源共用的
通用尾綴詞陣列**（`大學`/`學院`，用於動態組出句型 regex）整個**覆蓋**成一批**特定於單一
來源、單一時間點抓到的具體專有名詞**——兩者的變動頻率、抽象層級、使用方式完全不同，
混在同一個陣列裡會讓其中一種用途在毫無警示的情況下失效。未來任何要往
`exclude_terms.json`/`profile.json` 的陣列新增詞彙時，必須先確認：(1) 這個詞是「通用
句型辨識用」還是「特定個案排除用」；(2) 現有陣列裡是否已經有性質不同的詞彙混雜；
(3) 新增操作是 `push`（追加）還是意外的整個陣列覆蓋——`addExcludeTerm()`（本專案既有的
Discord 對話式管理機制）本身是安全的 `push` 操作，不會覆蓋既有內容，如果未來又出現類似
的整個陣列被取代情況，代表變更**沒有透過這個既有機制**、而是繞過它直接編輯 JSON 檔案，
這本身就是需要留意的警訊。

---

## 2026-07-05（續）：簡化輸入需求的決策原則——簡化參數 vs AI 自動分類

使用者要求 `/排除關鍵字新增` 不需要每次都指定「分類 子分類 語言」三個參數，只需提供關鍵字
本身。這類「簡化輸入」需求表面上有兩種實作路線，本輪明確選擇其中一種，並記錄取捨依據供
未來若再出現類似需求時參考：

**路線 A（本輪採用）：簡化參數，固定寫入一個「夠通用、風險夠低」的既有機制**。本專案
`config/profile.json` 的 `exclude_keywords` 本來就是「純字串 substring 比對、涵蓋範圍最廣」
的第一層硬性排除機制，剛好可以當作「使用者沒有指定分類時」的合理預設落點——不需要判斷
使用者的意圖，單純降低操作門檻。

**路線 B（本輪明確排除）：系統自動判斷輸入該歸類到哪一種既有分類**。例如自動判斷一個關鍵字
是「校名」還是「科系」還是「身份別」，再自動寫入 `exclude_terms.json` 對應的
`school_restricted`/`special_status` 等結構化分類。**這條路線在本質上是語意理解任務**——
「這個詞屬於哪一種限制類型」沒有簡單的字面規則可以判斷（就像 `school_restricted` regression
那次，連程式碼作者本人都需要實測+人工比對才能正確分類 11 個詞彙，不是靠字面特徵就能自動
決定的事），會逾越使用者訂下的「AI 僅限來源合法性判斷，不得用於核心排除/計分邏輯」硬性限制
（見「AI 使用限制的解除範圍」章節）。

**Universal Principle（供未來類似「簡化輸入」需求判斷取捨）**：當使用者要求「減少輸入時
需要提供的資訊量」時，先問一個問題——「省略的那個資訊，是靠語意理解才能還原，還是可以用
一個安全、通用的預設值取代？」。如果答案是後者（本例：省略「分類」時，落到「純字串比對、
涵蓋最廣」的既有第一層機制），直接採用固定預設值即可，不需要也不應該做任何形式的自動語意
判斷；如果答案是前者（真的需要理解語意才能決定該怎麼處理），則應該老實回報「這個簡化需求
需要語意判斷，逾越目前 AI 使用範圍限制，需要使用者明確授權才能討論是否/如何解除限制」，而
不是自己想辦法用規則式 heuristic 硬湊一個看似能用但其實脆弱的判斷邏輯。**兩者的分界線是
「有沒有一個安全、通用、不需要理解上下文語意的預設值可以套用」**，不是「實作起來難不難」。
