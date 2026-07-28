# 個人專用獎學金監測系統（風險收斂版）

原企劃書 v1 的架構調整版：移除 n8n 與常駐 Ollama，改用 node-cron + Rule Engine。
本文件已同步實際程式碼現況（先前版本描述的部分功能只存在企劃階段，尚未真正實作）。

## 與原企劃書的關鍵差異（現況）

| 項目 | 原方案 | 本版本現況 | 說明 |
|---|---|---|---|
| 排程/自動化平台 | n8n (Docker service) | node-cron（`src/scheduler.js`） | 每 6 小時爬取一次、每日 09:00 日報、每週日 18:00 週報，全部在同一個 process 裡註冊 |
| 資格判斷 | 布林 matched | **六項硬性排除閘門（AND 邏輯）+ 關鍵字權重加總 ≥ threshold** | 沒有 0~1 信心分數。**目前系統唯一的篩選基準鎖定六項條件：國籍、戶籍、學校、科系、年級、家境**，六項全部是第一層強制排除（命中即剔除，不進計分層，無論第二層關鍵字分數多高都不放行），只有全數通過才會進入第二層計分（`rules.json` 中英文關鍵字加總 ≥ threshold）。詳見下方「硬性排除規則（六項條件）」段落——這取代了先前「部分條件為硬性、部分僅供參考」的舊描述 |
| 資料儲存 | 原企劃書未定 | JSON 檔（`data/db.json`、`data/sources.json`、`data/runs.json`，皆 gitignored） | 因 `better-sqlite3` 在 Windows 上編譯需要 Visual Studio C++ toolset、多次失敗，已放棄 SQLite，改用純 JSON 檔 + 原子寫入（先寫 `.tmp` 再 rename）。`data/sources.json` 混合了靜態設定與 score/lastChecked 等執行期產物，已拆成 `config/sources.seed.json`（靜態、進 git，只在 `data/sources.json` 不存在時 seed 一次）+ `data/sources.json`（純執行期狀態，gitignored），避免每次爬取都讓 git status 變髒 |
| AI 判讀（模糊地帶） | 「系統無法判斷」時觸發 AI（未定義觸發條件） | **尚未實作** | `discoveryAI.js`（自動擴充來源）與舊版 `sourceAI` 的 AI 判讀構想目前都不存在；`src/sourceAI.js` 只是給既有來源打健康分數（0~10），不是對獎學金項目做資格判讀 |
| 日期擷取 | 未處理 | **尚未實作** | 目前兩個來源（MOE、DAAD）都沒有可靠的公告發布日/截止日欄位可擷取，`publishDate` 固定回傳 `null`；MOE 頁面雖有「申請期間(迄)」欄位，但格式為民國年，尚未做轉換與擷取 |
| 系統健康 | 無 | `src/runLog.js` + `data/runs.json` | 每次爬取後記錄各來源的 status/筆數；日報最上方會列出目前異常的來源 |
| 電腦休眠遺漏 | 未處理 | 啟動時檢查 `runLog.getLastSuccessAt()`，超過 8 小時自動補跑一次 | 個人電腦非 24/7 開機的現實情境，已在 `scheduler.js` 實作並測試 |
| 個人條件更新 | 需重寫程式碼/重啟服務 | Discord 私訊指令 `/update 欄位=值` 即時生效 | `ruleEngine.js` 每次 `filter()` 都用 `fs.readFileSync` 現讀 `rules.json` 與 `profile.json`，不用 `require()` 快取，已實測確認同一 process 內更新後立即生效 |
| 報告/告警推播 | Telegram Bot API | Discord Bot 私訊（`src/discordBot.js`） | 見下方「通知與互動」說明 |
| 網站改版偵測 | 無 | 已實作邊緣觸發告警 | `status='empty'` 會記錄在 `data/runs.json`，也會拉低 `sourceAI` 健康分數（低於門檻自動 disable 該來源）；`scheduler.js` 每次爬取後會比對「這次」跟 `runLog` 裡「上次」的 status，只在**該來源由 `ok` 轉為 `empty` 的那一刻**觸發一次 Discord 告警（`sendRedesignAlert`），之後連續多次都是 `empty` 不會重複洗版，直到恢復 `ok` 才會再發一則「已恢復」通知並重新武裝下一次的邊緣觸發 |

## 通知與互動：單一 Discord Bot（`src/discordBot.js`）

全部功能都透過與 Bot 的**私訊 (DM)** 完成：

| 用途 | 觸發方式 |
|---|---|
| 每 6 小時爬取一次（`src/scheduler.js` cron） | 自動執行，結果寫入 `data/db.json`，不逐筆推播 |
| 每日 09:00 報告（`src/report/generateReport.js`） | 排程自動私訊推播當日新增項目 + 系統健康摘要，本輪改版見下方「訊息四欄位格式」 |
| 每週日 18:00 週報 | 排程自動私訊推播本週新增項目彙整 |
| 啟動時補跑（距上次成功執行 > 8 小時） | `npm start` 啟動當下立即執行一次爬取 |
| 系統健康告警（來源爬取拋出例外） | 偵測後立即私訊推播 |
| `/myprofile` | 查詢目前篩選條件（無參數） |
| `/update 欄位=值` | 更新條件，例如 `/update gpa=3.8`（欄位需在 `UPDATABLE_FIELDS` 清單內，見「個人條件動態更新」章節） |
| `/rules` | 查詢目前關鍵字權重與 threshold（無參數） |
| `/rule 關鍵字 分數` | 調整單一關鍵字權重（**兩個參數用空格分隔，分數必須是整數**），例如 `/rule 獎學金 3`；調整 threshold 則用 `/rule threshold 2` |
| `/排除關鍵字 詞彙`（本輪新增，簡化版） | 新增一筆純字串排除關鍵字至 `config/profile.json` 的 `exclude_keywords`（**單一參數，可含空白**），例如 `/排除關鍵字 限清華大學`；不需要指定分類，適合快速排除、不在意精確歸類的情境（詳見「快速排除關鍵字」章節） |
| `/移除排除關鍵字 詞彙`（本輪新增，簡化版） | 移除對應的簡化版排除關鍵字，格式同上 |
| `/排除關鍵字新增 分類 子分類 語言 詞彙`（完整版） | 新增一筆 `config/exclude_terms.json` 排除詞彙（**4 個參數用空格分隔**），例如 `/排除關鍵字新增 school_restricted name_suffix zh 測試學院`；找不到對應分類/子分類會明確回覆錯誤，不會憑空建立新分類。需要精確控制歸類（例如要確保某詞彙走 `special_status`/`school_restricted` 等結構化比對邏輯）時使用 |
| `/排除關鍵字移除 分類 子分類 語言 詞彙`（完整版） | 移除對應的排除詞彙，格式同上 |
| `/排除關鍵字清單` | 依「分類.子分類.語言」分組列出目前所有排除詞彙（無參數），避免像先前的 `/查詢獎學金`/`/篩選獎學金` 一樣讓使用者只能盲猜 |
| `/sources` | 查詢目前爬取來源清單（無參數） |
| `/擷取引用連結 類別`（本輪新增） | 對指定的既有來源頁面，用純 DOM/正則抽取頁面內出現的外部連結（排除內部連結/社群媒體/廣告追蹤網域/已在追蹤清單裡的網址），例如 `/擷取引用連結 東海大學獎助學金`；**只是收集候選連結，不判斷是不是獎學金網站**，結果需自行逐一用 `/驗證來源` 驗證（詳見「來源擴充範圍界定」章節） |
| `/驗證來源 網址`（本輪新增） | 批次驗證候選來源（連線可用性、反爬蟲阻擋偵測、SPA 偵測、`GenericCrawler` 保底邏輯實際抓取診斷），例如 `/驗證來源 https://example.com/scholarships`；**驗證通過不會自動加入**，需要自行執行下方的 `/addsource` 才算正式加入——這是使用者主導的批次驗證，不是系統自主探索新網站（詳見「來源擴充範圍界定」章節） |
| `/addsource 名稱 網址` | 新增來源（**兩個參數用空格分隔，網址須是完整 `http(s)://` 開頭**），例如 `/addsource Erasmus https://erasmus-plus.example.com/scholarships` |
| `/removesource 網址` | 移除來源（**參數是完整網址，須跟 `/sources` 查到的網址完全一致**），例如 `/removesource https://erasmus-plus.example.com/scholarships` |
| `/查詢獎學金 關鍵字`（本輪新增） | 依標題關鍵字搜尋 `data/db.json` 累積的所有項目（**關鍵字不能含空白**，例如 `/查詢獎學金 獎學金`；**純文字即可，不需要加中括號**——中括號只是本文件裡表示「這裡填你要的值」的佔位符號，不是實際語法），已排除截止日不足 1 週的項目（見下方「截止日過濾規則」）。**全部符合的筆數都會送出**（超過 Discord 單則訊息 2000 字元上限時自動分成多則訊息，先前曾誤砍到只顯示前 20 筆，本輪已修復） |
| `/篩選獎學金 類別`（本輪新增） | 依來源分類篩選（**類別需完全等於**下列其中之一：`教育部圓夢助學網`／`DAAD（德國）`／`東海大學獎助學金`／`European Funding Guide（歐洲）`，不確定可用 `/獎學金類別` 查詢），例如 `/篩選獎學金 東海大學獎助學金`，已排除截止日不足 1 週的項目，全部符合筆數都會送出（同上，多則訊息分段）。含空白的類別名稱（例如 `European Funding Guide（歐洲）`）已於 2026-07-05 修復可正確比對 |
| `/依地區查詢 地區`（本輪新增） | 依「地區」查詢，比 `/篩選獎學金` 更粗的分組——**一次可涵蓋多個來源**（例如「台灣」同時包含教育部圓夢助學網跟東海大學獎助學金），提供跟 `/篩選獎學金` 不同的查詢粒度，不是重複功能。目前 3 個地區：`台灣`（教育部圓夢助學網＋東海大學獎助學金）／`德國`（DAAD）／`歐洲`（European Funding Guide），例如 `/依地區查詢 台灣` |
| `/獎學金地區`（本輪新增） | 查詢 `/依地區查詢` 可用的地區清單 |
| `/獎學金說明 編號`（本輪新增） | 顯示上一次 `/查詢獎學金`、`/篩選獎學金` 清單中對應編號項目的完整四欄位詳情，例如先 `/查詢獎學金 獎學金` 再 `/獎學金說明 1`（**純數字即可，不需要加中括號/句點/逗號**——`[11]`、`11.`、`11,` 這類裝飾符號本輪已修復可容錯解析，解析不出數字時會明確回覆錯誤訊息而非完全無反應；編號對應清單順序，必須先查詢/篩選過才有清單可查，機器人重啟後清單會清空） |
| `/獎學金類別`（本輪新增） | 查詢 `/篩選獎學金` 可用的類別清單，直接複用 `/sources` 背後的同一份來源資料，不是另外寫死一份清單 |
| `/獎學金關鍵字`（本輪新增） | 查詢 `/查詢獎學金` 的常見關鍵字建議，直接複用 `/rules` 背後的計分權重清單，不是另外寫死一份清單；`/查詢獎學金` 本身不限於這份清單，任何標題裡出現的字都能搜尋 |
| `/help` | 列出可用指令（無參數） |

✅ **統一輸入格式錯誤處理（2026-07-05 已修復）**：先前上表大多數指令對「格式
不完整/不符合語法」的輸入是完全無回應，只有 `/獎學金說明` 有明確錯誤訊息。
已新增 `matchOrUsageError()` helper 統一套用到全部 12 個帶參數指令
（`/update`、`/rule`、`/排除關鍵字新增`、`/排除關鍵字移除`、`/驗證來源`、
`/擷取引用連結`、`/addsource`、`/removesource`、`/查詢獎學金`、`/篩選獎學金`、
`/依地區查詢`、`/獎學金說明`）——只要看起來是要打某個指令但格式不符，一律回覆
「❌ 格式錯誤，正確用法：...」，不再靜默無回應。詳見 `Meta_Dev_Knowledge.md`
記錄的 Universal Principle 與完整測試結果。

### 截止日過濾規則（本輪新增，`src/report/format.js` 的 `filterByDeadline()`）

使用者要求：所有回報的獎學金，距今到截止日需至少 1 週，不足 1 週的不列出。**SCAN
發現** `data/db.json` 目前累積的 140 筆真實記錄中，`deadline` 欄位**有效率是
0%（0/140，四個來源皆是）**——四欄位詳情頁擷取自上輪上線以來還沒有一次真的觸發過
（見「訊息四欄位格式」章節的已知限制）。若把 `deadline` 為 `null` 也當「不足 1 週」
排除，會讓目前所有查詢/報告瞬間變成 0 筆結果，這既不合理也不是使用者的原意——
「缺乏日期資訊」不等於「已過期」。因此規則設計為：

- `deadline` 有實際值，且距今 < 7 天（含已過期，即距今為負數）：**排除**
- `deadline` 有實際值，且距今 ≥ 7 天：正常顯示
- `deadline` 為 `null`（未提供）：**正常顯示**，截止日期那一行會顯示
  「未提供（⚠️ 請自行至原始連結確認）」，由使用者自己判斷，而非由系統代為隱藏
  （原本「未提供」跟警語分成兩行顯示過於冗長重複，本輪已整併成同一行）

套用範圍一致：`/查詢獎學金`、`/篩選獎學金`、每日/每週報告皆呼叫同一個
`filterByDeadline()`，避免各處標準不一致。**目前實際效果**：因為 `deadline`
有效率是 0%，這個規則目前對現有 140 筆真實資料**沒有任何篩選作用**（全部因
`deadline=null` 而正常顯示，帶警語），要等詳情頁擷取真的抓到有效 `deadline`
值的新項目出現才會實際發揮排除效果。

⚠️ **這個 Bot 從第一輪開始就沒有用過 discord.js 的 `SlashCommandBuilder`／
`interactionCreate`**，所有「指令」都是私訊裡的純文字、由 `messageCreate` +
正則比對解析（見 `handleMessage()`）。本輪與先前輪次新增的中文指令沿用同一套機制，
不是註冊過的原生 Discord 應用程式指令，純粹是文字慣例。

僅回應 `.env` 中 `DISCORD_USER_ID` 對應帳號的私訊，其餘訊息一律忽略；非白名單來源的 DM
會寫入 `work/activity.log`（`discordBot:unauthorized`，含來源使用者 ID 與前 200 字訊息摘要）供事後稽核，
但 Bot 完全不回應。

⚠️ **寫入類指令有 rate limit**：`/update`、`/rule`、`/addsource`、`/removesource`、
`/排除關鍵字新增`、`/排除關鍵字移除`（本輪新增這兩個進 rate limit 清單）同一使用者
每 60 秒最多 5 次（滑動視窗），超過會收到「操作過於頻繁，請稍後再試」並記錄到
`work/activity.log`（`discordBot:rateLimited`）。查詢類指令（`/myprofile`、`/rules`、
`/sources`、`/驗證來源`、`/排除關鍵字清單`、`/獎學金類別`、`/獎學金關鍵字`、`/help`）
不受影響。這是為了在 Token 外洩、本人帳號被盜用的情境下，讓白名單機制失效時仍有一道防線。

**稽核 log 一覽**（皆寫入 `work/activity.log`，格式統一為 `logger.activity(事件名, detail)`）：

| 事件名 | 觸發時機 | detail 內容 |
|---|---|---|
| `discordBot:update` | `/update` 成功執行 | `userId`、`field`、`oldValue`、`newValue` |
| `discordBot:unauthorized` | 收到非白名單來源的 DM | `userId`、`messageSummary`（前 200 字） |
| `discordBot:rateLimited` | 寫入類指令超過 rate limit | `userId`、`command`（完整指令內容） |

Discord 單則訊息上限 2000 字元，`discordBot.js` 已實作自動分段（依報告內的
`━━━━━━━━━━` 分隔線切割），不會截斷內容。

## 個人條件動態更新（Discord 私訊）

```
/myprofile              查看目前篩選條件
/update gpa=3.8          更新單一欄位
/update major=Engineering
/help                    列出可用指令與欄位
```

可更新欄位：`country`, `current_country`, `degree`, `major`, `grade`, `gpa`, `income_status`,
`residence_city`, `school`, `is_indigenous`, `is_overseas_student`, `is_refugee`,
`is_disability_or_illness`（本輪新增）
（`exclude_keywords`、`major_aliases`、`ai_fallback_score_range` 為結構化欄位，不開放純文字指令更新，需直接編輯 `config/profile.json`；
`ai_fallback_score_range` 目前僅為保留欄位，尚未有對應的 AI 判讀邏輯讀取它。`is_indigenous`/
`is_overseas_student`/`is_refugee`/`is_disability_or_illness` 是布林值，`/update is_disability_or_illness=true`
這樣更新，`discordBot.js` 會自動轉型，不是存成字串 `"true"`）

### 硬性排除規則（六項條件，`src/ruleEngine.js`，純規則式字串/正則比對，不涉及語意判斷）

**這六項是目前系統唯一的篩選基準，AND 邏輯、缺一不可**——公告只要明確跟其中任一項衝突就直接剔除，
不進計分層，無論 `rules.json` 關鍵字分數多高都不放行。若公告完全沒提到某項條件（例如沒講戶籍地），
視為對所有人開放，不會因為「沒明確包含台中」而被排除（觸發條件是「明確衝突」，不是「未明確提及」）。

⚠️ **本輪改動**：六項函式的比對詞彙（不含結構性防假陽性邏輯）已全部改讀 `config/exclude_terms.json`
統一字典，取代原本分散寫死在各函式裡的字串陣列，詳見下方「多語言排除字典」章節。

| # | Profile 欄位 | 用途 | 排除條件 |
|---|---|---|---|
| 1 | `country`（Taiwan） | 國籍 | 讀取 `config/exclude_terms.json` 的 `nationality_restricted`：命中「限OO國籍/籍(學生)」且 OO 不是台灣/臺灣/中華民國；**本輪新增**命中英文「for Greeks」「for Hellenes」這類 demonym 句型（**實測** EFG 語料 2 筆真實命中，僅收錄實測出現過的 demonym，見下方「多語言排除字典」）。⚠️ 本輪已把「僑生」判斷移出這條規則，改歸類進第 7 項「特定身份別」（見下）。**本輪修復一個既有 bug**：`限([一-龥]{2,6})(?:國籍|籍)` 的「籍」分支曾誤判「限台中市**設籍**」為國籍限定（貪婪比對回溯把「設」字吃進國名捕獲群組），現已用「國名不含市/縣/設/戶」的特徵排除誤判 |
| 2 | `residence_city`（台中市） | 戶籍地 | 公告標題命中「限OO市/縣設籍」，且 OO 不是台中市（已處理「台/臺」寫法差異）。**SCAN 結果**：抽樣 DAAD/EFG 語料確認無外語版本地區限定用語，此項無字典詞彙可讀，維持原本純結構化 regex |
| 3 | `school`（東海大學） | 就讀學校 | 讀取 `exclude_terms.json` 的 `school_restricted.name_suffix`（大學/學院尾綴詞）動態組出比對 pattern：命中「限OO大學(在學)學生」「限OO大學(在校)在校生」，且 OO 不是東海大學；也涵蓋「限本校（實際校名）」括號澄清句型，以及「僅限台大、政大在學生」這種頓號列舉簡稱的句型（頓號需≥2項才放寬尾綴要求的防假陽性邏輯維持寫在程式碼裡，不受字典重構影響，見下方 EXEC A 說明）。⚠️ **重要區分**（本輪釐清）：「大學」「學院」這兩個尾綴詞是**輔助辨識句型結構**用的（判斷「限OO大學」裡的 OO 是不是一個具體校名），**不是**直接排除觸發詞本身——標題單純出現「大學」「學院」但沒有搭配「限...在學/在校學生」句型（例如「大學部獎學金開放申請」）不會被這條規則排除，已用真實測試案例驗證（見下方 DEBT 已修復條目） |
| 4 | `major`（社會學系）+ `major_aliases` | 科系 | 沿用既有 `exclude_keywords`（限理工/限醫學/限博士/限研究所）反向排除清單；**實測**抽樣 332 筆真實資料，沒有找到「限工程」「限商管」「限法律」「限醫護」等額外的科系排他句型（唯一「限」字命中是「…股份**有限**公司」，跟科系無關），故當時未擴充清單——不得憑空列舉未實際出現過的詞。**本輪新增** 4 個具體系所關鍵字（體育/美術系/國貿系/會計系）——這批是從 THU 語料裡實際出現的具體科系限定獎學金標題（例如「美術系吳學讓國畫獎學金」「國貿系林財丁教授獎助學金」）抽出的系所名稱，原本被誤放進 `school_restricted.name_suffix.zh`（見 DEBT「已修復」條目），本輪歸位到這裡；跟原有 5 個「限X」句型不同，這批是抓取具體系所名稱本身（無「限」字），比對粒度更細，兩者共用同一份 `exclude_keywords` 陣列、同一套純 substring 比對邏輯，不衝突 |
| 5 | `degree`（Bachelor）+ `grade`（2） | 年級/學制 | 讀取 `exclude_terms.json` 的 `grade_restricted`：命中「博士生/研究生/碩士/phd/master/doctoral/postdoc」等研究所限定字樣（英文維持原本 `\b` 詞界+大小寫不敏感），**本輪新增**命中德文學術用語「Promotion」（**實測** DAAD 語料 3 筆真實命中，大小寫敏感比對避免誤判英文一般語意的 promotion，見下方「多語言排除字典」），且使用者非研究所學生；命中「限大三/大四」且使用者是大二以下（**[未驗證]**，無真實案例）；命中「新生入學/優秀新生」（剛入學新生限定）且使用者非大一（已排除「新生代」誤判）。年級 vs 學制動態比對 `profile.degree`/`profile.grade` 的防假陽性邏輯維持寫在程式碼裡，不受字典重構影響 |
| 6 | `income_status`（normal） | 家庭經濟狀況 | 讀取 `exclude_terms.json` 的 `income_restricted`：公告標題含「低收入戶」「中低收入戶」「清寒」，且 `income_status` 不是這些低收入層級。**SCAN 結果**：抽樣 DAAD/EFG 語料確認無外語版本的 need-based/低收入限定用語，此項本輪無新增 |
| 7（新增） | `is_indigenous`（false）+ `is_overseas_student`（false）+ `is_refugee`（false）+ `is_disability_or_illness`（false）+ `is_new_resident`（false，本輪新增） | 特定身份別 | 讀取 `exclude_terms.json` 的 `special_status`：公告標題含「原住民」且使用者非原住民身份；含「僑生」且使用者非僑生身份；含英文「refugee」且使用者非難民身份（**實測** DAAD 語料 2 筆真實命中：「Scholarship for Students With Refugee Status」「HessenFonds for Refugees and Researchers at Risk」）；含「罕見疾病」「視障」「癌症」「身心障礙」「身障」「重大傷病」「特殊疾病」且使用者非該身份（**實測** 457 筆真實標題找到 3 筆真實命中：MOE「2026罕見疾病獎助學金」、THU「愛盲基金會清寒視障學生助學金」、THU「台灣癌症基金會VS遠雄人壽」——最後一筆標題本身看不出資格限定，抓取詳情頁後確認「其他限制條件」寫明「父、母或本人罹患癌症目前治療中或完成治療兩年內」，證實非單純贊助機構掛名；「身心障礙」「身障」「重大傷病」「特殊疾病」為使用者建議詞，457 筆抽樣未實際出現，**[未驗證]**）；**本輪新增**含「新住民」且使用者非新住民身份（**實測** THU 語料 2 筆真實命中：「新住民及其子女培力與獎助學金」「新應材新住民子女獎學金計畫」——這兩個詞原本被誤放進 `school_restricted.name_suffix.zh`，本輪歸位到這裡，見 DEBT「已修復」條目）。⚠️ **已知風險**：「癌症」這類詞若未來出現「XX癌症基金會贊助但無疾病限定」的反例會誤判，跟 nationality 規則踩過的「German National Academic Foundation」贊助機構名稱陷阱同一種風險類型，目前只有 1 筆真實樣本、已用詳情頁驗證過。邊界案例：若標題明確寫「...優先，餘缺開放一般生」則不排除（五個身份類別共用同一個防護），此複合句型 457 筆真實資料裡沒有出現（**[未驗證]**，依任務指示的定義先寫好） |

**目前最新通過筆數**（`data/db.json` 140 筆現存記錄，本輪修復 `school_restricted` regression + 科系/新住民歸位後重新驗證）：MOE 6/DAAD 16/THU 71/EFG 28（THU 從 85 降為 71，少的 14 筆全數對應到本輪新增的科系關鍵字/個案排除/新住民身份規則正確生效，逐筆核對非誤判，見 DEBT「已修復」條目的完整清單；MOE/DAAD/EFG 三者不受影響）。

⚠️ **已知限制**：以上規則只能比對公告「標題」，因為 crawler 目前不擷取公告內文（MOE/DAAD/東海大學都沒有可靠的內文欄位可用）。若限制條件只寫在內文裡，這幾條規則會漏放。

### 快速排除關鍵字（本輪新增）

使用者要求 `/排除關鍵字新增` 不需要每次都指定「分類 子分類 語言」三個參數，只需提供關鍵字本身。

**設計決策：簡化參數，非自動分類判斷**。曾考慮的替代方案是讓系統自動判斷一個關鍵字該歸類到
`school_restricted`（學校）/`exclude_keywords`（科系）/`special_status`（身份別）哪一類，但這
屬於語意理解任務，會逾越使用者既有的「AI 僅限來源合法性判斷」硬性限制範圍（見「AI 使用限制的
解除範圍」章節），因此**明確排除**這個方向。改為新增兩個簡化指令：`/排除關鍵字 詞彙` /
`/移除排除關鍵字 詞彙`，固定寫入 `config/profile.json` 的 `exclude_keywords`——這是系統最初
設計的「第一層硬性排除」，純字串 substring 比對（`ruleEngine.js` `filter()` 裡的
`excludeKeywords.some((kw) => lowerTitle.includes(...))`），涵蓋範圍最廣、比對邏輯最簡單，
風險最低，不需要使用者理解 `exclude_terms.json` 的三層巢狀結構就能用。

**兩種指令並存，使用時機不同**：
- **簡化版**（`/排除關鍵字`/`/移除排除關鍵字`）：快速排除、不在意精確歸類時使用，例如臨時想
  排除某個特定校名或某個特定獎學金全名
- **完整版**（`/排除關鍵字新增`/`/排除關鍵字移除`，4 參數）：需要精確控制歸類到
  `exclude_terms.json` 的結構化比對邏輯時使用，例如要確保某個身份別詞彙走
  `special_status`（會用 `profile.is_xxx` 欄位動態判斷是否豁免），而不是單純 substring 比對

**實作**：沿用 `/update` 既有的 `readProfile()`/`writeProfileAtomic()`（暫存檔+rename 原子寫入）
讀寫機制，不另開一套平行邏輯；兩個新指令都加進 `WRITE_COMMAND_PATTERNS`，跟其他寫入類指令
共用同一套 rate limit（每分鐘 5 次）與 `activity.log` 稽核紀錄（`discordBot:excludeKeywordAdd`/
`discordBot:excludeKeywordRemove`）；新增前檢查是否已存在，避免重複新增造成陣列膨脹；
`/myprofile` 同步顯示 `exclude_keywords` 目前內容，避免使用者只能從每次新增/移除的回覆訊息
片段拼湊全貌。

### 多語言排除字典（`config/exclude_terms.json`，本輪新建）

**設計理由**：六項排除函式原本各自把「用哪些字詞判斷排除」寫死在程式碼裡（例如 `isExcludedByIncome()`
內的 `["低收入戶", "中低收入戶", "清寒"]`）。來源語言只會持續增加（本輪已從純中文/英文擴充到德文），
分散在 6 個函式裡個別加陣列，維護成本會隨語言數線性惡化——**估算**：分散寫法（方案 A）每加一種
語言，平均要改到 2-3 個函式（本輪的 3 個真實案例：demonym 只碰 `isExcludedByNationality()`、
refugee 只碰 `isExcludedBySpecialStatus()`、Promotion 只碰 `isExcludedByGrade()`，各自獨立變更，
之後若某天要幫這三類同時擴充第 4 種語言，需要在 3 個不同函式裡各改一次，還要各自重新過一次
code review）；統一字典（方案 B，本輪採用）把新增語言縮減成「在對應分類底下加一個語言鍵值」
（例如 `nationality_restricted.demonym_restricted.fr`），只改 1 個 JSON 檔、不用碰 `ruleEngine.js`
的程式邏輯本身，也不需要重新過函式邏輯的 code review。這跟 `config/rules.json` 的計分權重已經是
資料驅動、可用 Discord `/rule` 調整的精神一致。

**涵蓋範圍**：`income_restricted`（低收入/清寒）、`special_status`（原住民/僑生/難民）、
`nationality_restricted`（國籍別名/demonym）、`school_restricted`（校名尾綴詞）、
`grade_restricted`（研究所限定/大三大四限定/新生限定）、`residence_restricted`（SCAN 後確認
無字典詞彙可搬，維持純結構化 regex）。目前涵蓋 **3 種語言**（zh/en/de）。

**刻意不搬進字典的部分**：頓號列舉需≥2項才放寬校名尾綴（`isExcludedBySchool()`）、年級 vs 學制
動態比對 `profile.degree`/`profile.grade`（`isExcludedByGrade()`）——這些是控制流程判斷，不是
詞彙清單，搬進字典只會讓邏輯分散在兩個地方、更難維護，也違背「不得為了統一字典而犧牲先前已
修好的假陽性防護」的要求。

**本輪 SCAN 新增的真實外語詞彙**（抽樣 DAAD 163 筆 + EFG 125 筆語料，只收錄實測出現過的詞）：

| 分類 | 語言 | 詞彙 | 真實出現次數 | 範例標題 |
|---|---|---|---|---|
| `nationality_restricted.demonym_restricted` | en | Greeks, Hellenes | 2 | `CERN Scholarships for Greeks`、`Onassis Foundation: Scholarships for Hellenes` |
| `special_status.refugee` | en | refugee | 2 | `Scholarship for Students With Refugee Status`、`HessenFonds for Refugees and Researchers at Risk` |
| `grade_restricted.graduate_only` | de | Promotion | 3 | `...BECAL-DAAD Promotion`、`...COMECYT-DAAD Promotion`、`...COLFUTURO Promotion`（皆有對應的 `...Master` 姊妹項目可互相對照，證實 Promotion 是穩定的博士班軌道標籤） |

**SCAN 後確認無缺口的分類**（如實記錄，非偷懶漏掃）：`income_restricted`（0 筆外語 need-based 用語）、
`residence_restricted`（0 筆外語地區限定用語）、`school_restricted`（0 筆外語「限定就讀某校」句型）。

**實測效果**：這 3 個新增 pattern 在 457 筆真實語料中正確命中對應的 7 筆標題（見上表），但因為這些
標題原本在 `scoreItem()` 計分層就低於 `threshold=2`（例如 "CERN Scholarships for Greeks" 只命中
`scholar` 1 分），所以本輪的通過筆數（MOE 7/DAAD 16/THU 85/EFG 33，**此數字為當時該輪結果，
後續「訊息四欄位格式」章節新增的身心障礙/疾病排除規則已讓 MOE 變成 6，見該章節與 DEBT
表最新數字**）**維持不變**——新增的硬性排除是防禦性正確性保證（保證未來 `rules.json`
權重調整後這些項目也不會誤放行），不是本輪立即可見的
篩選效果。

### EXEC A：`isExcludedBySchool()` 漏報根因與修復

使用者回報「仍被推薦限他校獎學金」後，直接拿任務給的例句測試舊版 regex
（`/限([一-龥]{2,10}(?:大學|學院))(?:在學)?學生/`），確認三個具體根因（不是憑空重寫）：

1. **「限本校（清華大學）在校生」比對失敗**：「限」跟真正校名之間插了「本校（」三個字，原本要求「限」緊接校名的比對邏輯直接斷裂
2. **「僅限台大、政大在學生」比對失敗**：台大/政大是常見簡稱（取全名前兩字），完全不含「大學」「學院」這兩個字，原本要求的尾綴比對不到；而且是頓號列舉兩間學校，原本的 regex 只處理單一校名
3. **「在校生」完全沒有涵蓋**：只接受「在學學生」「學生」當結尾

修復時發現一個**必須避免的陷阱**：為了支援簡稱（台大/政大），第一版把校名尾綴要求整個拿掉，
結果變成「限台北市設籍學生」「限清寒學生」「限研究所學生」這些**跟學校完全無關**的句型也被
誤判成學校排除（因為它們也是「限＋兩三個字＋學生」的形狀）。最終修法：只有在**頓號列舉至少
兩個候選**（`僅限([一-龥]{2,4}(?:、[一-龥]{2,4})+)...學生`）時才放寬校名不需要「大學/學院」
尾綴——因為真實資料裡的城市/收入/年級限定都是單一數值、不會用頓號列舉，這是安全的判斷依據。
單一校名時仍要求「大學/學院」尾綴（維持原本零誤判的安全性）。

實測：12 組模擬標題（含 4 組任務要求的 + 8 組假陽性防護測試）全數符合預期；對 457 筆真實資料
重新跑一次，`isExcludedBySchool()` 排除筆數維持 **0 筆不變**（目前語料庫本來就沒有出現任何
學校限定句型，這次修復是純新增能力，非本輪造成的行為變化）。

⚠️ **重大真實資料影響（實測，非估算）**：加上國籍/年級兩項閘門後，MOE+THU+DAAD 332 筆重新跑
`filter()`，通過筆數從 **141 → 102**（少 39 筆：4 筆因國籍規則排除、35 筆因年級規則排除）。
**DAAD 受影響最大**：36 筆 → 6 筆（30 筆是 PhD/Master/Doctoral 等研究所層級獎學金，使用者是
大二學生不符資格，正確排除）——這反映一個既有的架構張力：`rules.json` 的計分權重從最早期就
把 `phd`(3分)/`master`(2分) 列為正向加分關鍵字，隱含系統原本是為研究所申請者設計的，跟現在
「大二學生」的實際 persona 不一致。本輪嚴格按六項條件的字面定義實作硬性排除，沒有連帶調整
`phd`/`master` 的計分權重（不在本輪範圍內），這兩個權重值現在對已被排除的項目形同虛設，
列為 DEBT 供後續評估是否要移除或調整。

### 計分權重（`config/rules.json`，中英文並存，可用 `/rule 關鍵字 權重` 調整）

第二層計分（通過第一層排除後才計分，分數 ≥ `threshold` 才算符合資格）原本只有英文關鍵字
（phd/master/germany/scholar），對中文標題來源（MOE、東海大學）幾乎不可能命中。已從 MOE（15 筆）
+ 東海大學（154 筆）共 169 筆真實標題實際歸納出現頻率最高的中文詞，補上：

| 關鍵字 | 權重 | 真實出現次數（169 筆中） |
|---|---|---|
| 獎學金 | 2 | 78 |
| 助學金 | 2 | 37 |
| 獎助學金 | 2 | 23 |
| 優秀 | 1 | 25 |
| 基金會 | 1 | 22 |
| 清寒 | 1 | 19 |
| 慈善 / 文教 | 1 | 10 / 10 |
| 紀念 | 1 | 9 |
| 補助 | 1 | 7 |
| 原住民 / 僑生 | 1 | 5 / 5 |
| 就學 / 博士 | 1 | 4 / 4 |
| 弱勢 | 1 | 3 |

**實測效果**：MOE+東海大學 169 筆真實資料重新跑 `filter()`，通過計分門檻筆數從 **0 → 105**；
DAAD 163 筆的通過筆數維持 **36 筆不變**（中文關鍵字對純英文標題沒有任何影響）。
剩餘 64 筆裡，19 筆是被上面「硬性排除規則」正確擋下（例如標題含「清寒」但使用者
`income_status` 不是低收入層級——這是預期行為，不是計分覆蓋不足）；真正因為關鍵字清單
沒覆蓋到而掉分的是 45 筆，幾乎都是「行天宮」「許姓宗祠」「中華永續農業協會」這類純機構/
廟宇專有名稱，標題本身不含任何通用獎學金字樣，純關鍵字比對本來就無法涵蓋（需要語意判斷
才能識別「這是不是獎學金」，但硬性限制不得使用 AI），屬預期限制。

> 📌 以上數字是**加入六項硬性排除閘門（國籍/年級）之前**的狀態（純計分層修復的效果）。
> 加上國籍/年級閘門後的最終數字（MOE+THU+DAAD 332 筆通過 141→102）見上方「硬性排除規則
> （六項條件）」段落；45 筆純關鍵字覆蓋不足的殘留缺口**數字不變**（國籍/年級閘門排除的是
> 另一批不同的標題，兩者互不重疊）。

⚠️ `\w`（正則的 word character）只匹配 `[A-Za-z0-9_]`，不含中文字——`discordBot.js` 原本
`/rule` 指令與其 rate limit 偵測都用 `\w+` 比對關鍵字名稱，會讓 `/rule 獎學金 3` 這種指令
完全比對不到、無法執行。已改用 `\S+`（非空白字元），實測 `/rule 獎學金 3` 可正確更新權重，
`/rules` 也能正確顯示更新後的值。

### 計分權重（第二輪擴充：European Funding Guide 的歐洲語言同義詞）

EFG 來源加入後實測 125 筆真實標題，通過六項閘門+計分門檻是 **0** 筆（見上方「來源清單」）。
根因是計分覆蓋缺口——多數標題只命中既有的 `scholar`（1分），未達 `threshold=2`。歸納 125 筆
真實出現頻率：

| 關鍵字 | 權重 | 真實出現次數（125 筆中） | 是否採用 |
|---|---|---|---|
| `grant` / `grants` | 2 | 31 / 24 | ✅ 採用（合併視為同一詞根，权重 2） |
| `stipendium`（德文「獎學金」） | 2 | 3 | ✅ 採用（核心詞同義詞，比照獎助學金） |
| `stipendien`（德文複數/屬格） | — | 1 | ❌ 未採用，出現次數低於既有取用門檻（最低 3 次） |
| `fellowship` | — | 1 | ❌ 未採用，同上 |
| `award` | — | 1 | ❌ 未採用，同上，且語意較廣泛（不一定專指獎助學金） |
| `bourse`（法文）/ `aide`（法文）/ `beca`（西文） | — | 0 | ❌ 未採用，這批樣本裡完全沒出現，不得憑空列舉 |

**實測效果**：EFG 125 筆重新跑 `filter()`，通過筆數從 **0 → 33**。人工核對通過標題（節錄）：
`University of Lapland - Freemover Scholarship:Travel Grant`、`ESL Sprachaufenthalte – Stipendium
für Durchschnittliche`、`Region of Picardie - International Mobility Grant`、`Federal Ministry
of Education and Research: Deutschlandstipendium` 等，皆為一般性/旅遊/交換用途的獎助金，
沒有發現誤判成研究所限定的項目（`grant`/`stipendium` 命中但仍是研究所限定的 3 筆標題，
已被年級閘門正確排除，見下方 DAAD 段落的驗證）。

⚠️ **實測發現跟預期不同的地方**：`grant`/`stipendium` 是通用英文/德文詞，不是中文專屬詞，
對既有的 DAAD 來源也有影響——**DAAD 通過筆數從 36→6→16**（前次六項閘門上線時已降到 6，
這次新增關鍵字後回升到 16，並非「不受影響」）。多出的 10 筆例如
`Federal Ministry of Education and Research: Deutschlandstipendium`（透過 `stipendium` 命中）、
`Research Grants in Germany`（透過 `grant` 命中），逐筆核對標題皆未見「PhD/Master/Doctoral」
字樣，六項閘門也沒有排除任何一筆，屬合理的覆蓋率提升而非誤判。MOE（7 筆）與東海大學
（89 筆）則確實不受影響（中文標題不含這兩個新關鍵字）。

## 訊息四欄位格式 + 詳情頁擷取（本輪新增，`src/crawler/detailParser.js`）

使用者要求把 Discord 訊息裡每筆項目改成固定的「身份要求／申請條件／申請辦法／截止日期」
四欄位格式。**SCAN 發現**：這四個欄位在資料管線裡完全不存在——`src/db.js` 的 schema
（`link/title/source/score/publishDate/foundAt`）從第一輪開始就只存公告「標題」層級的資料，
`publishDate` 恆為 `null`（所有 crawler 都沒有可靠的日期欄位可用，見既有 DEBT 記錄）。
若直接照字面實作四欄位格式，全部來源、每一筆都只能顯示「未提供」，不是實作 bug，
是架構從未擷取過這些資料——不得為了湊格式而造假內容，因此本輪追加了「詳情頁擷取」
這一層基礎建設。

**設計**：對 `ruleEngine.filter()` 之後真正會顯示給使用者的存活項目（不是全部 unseen
項目），額外對 `item.link`（詳情頁網址，crawler 本來就有存）發一次 request，解析出
四欄位。放在 filter() 之後才做，而非對全部 unseen 項目做，是刻意的效能取捨——詳情頁
擷取比列表頁貴很多，符合既有 Low-Compute/Lightweight 原則。

**SCAN 結果（逐來源詳情頁真實 DOM 結構，各抽樣 4 筆確認格式穩定）**：

| 來源 | 詳情頁結構 | 對應到四欄位 |
|---|---|---|
| MOE | `.title`（標籤）+ 緊接的 `.content`（值），共 17 個標籤欄位 | 身份要求←獎助身分+獎助資格+戶籍地限制+學制；申請條件←成績+限制條件；申請辦法←申請方式+申請說明；截止日期←申請期間（**民國年格式**，例如「115/05/21～116/12/31」，已 +1911 轉換為西元年） |
| THU | `<td>` 純文字以「標籤：內容」形狀出現 | 身份要求←獎助學門+獎助對象；申請條件←成績條件+其他限制條件；申請辦法←申請說明+應繳證件或附件；截止日期←學生申請日期（西元年格式，不需轉換） |
| DAAD | `<h3>標籤</h3><p>內容</p>` 成對出現 | 身份要求←Target Group+Academic Requirements；申請條件←Application Requirements；申請辦法←Application Papers；截止日期←Application Deadline（**常是自由文字**如「deadlines differ and may be requested at the individual institutions」，只有找得到具體日期才轉換，找不到就顯示「未提供」，不猜測日期） |
| EFG | Drupal `.field-name-field-*` class（**label 顯示文字依投稿者語言而異**，實測同時有英文「Deadline:」與德文「Bewerbungsschluss:」，但 class 名稱本身穩定，改用 class 選取） | 身份要求←`.field-name-field-eligible-country`；申請條件/申請辦法←**無穩定欄位可擷取**（混在自由文字說明裡，[未驗證]，顯示未提供）；截止日期←`.field-name-field-deadline`（常是「日.月.」無年份格式如「01.09.」，年份不明無法安全推算，顯示未提供；只有「日.月.年」完整格式才轉換） |

**分類分組**：SCAN 確認資料管線裡唯一四來源都穩定存在的分類欄位是 `item.source`
（列表頁網址）——MOE 的學制/學門、THU 的獎助學門等欄位都只存在單一來源，格式也不統一，
無法當跨來源共用的分類鍵。因此分類分組（`src/report/format.js` 的 `groupByCategory()`）
以來源名稱分組，而非細緻的獎學金類型分類；這是資料現況的誠實限制，非實作疏漏。

**⚠️ 效能取捨**：詳情頁擷取讓每次爬取的耗時隨「本次新增且通過六項閘門的項目數」
（而非全部 unseen 筆數）增加，每筆間隔 1 秒禮貌延遲。目前歷史紀錄每輪新增筆數多在
個位數~一百出頭（見 `work/activity.log`），換算下來單次排程可能增加數十秒到數分鐘，
仍在 `node-cron` 6 小時排程週期內可接受，但若未來新增筆數大幅增加需要重新評估。

## 來源清單（`config/sources.seed.json`，可用 `/sources` 查詢目前狀態）

| 來源 | 地區/類型 | 網址 | 抓取方式 | 實測筆數 |
|---|---|---|---|---|
| 教育部圓夢助學網 | 台灣・政府 | `edu.tw/helpdreams`（民間團體/政府機關兩分類） | 專屬 class（`src/crawler/sources/moe.js`） | 15（政府機關分類目前 0 筆） |
| DAAD | 德國・政府 | `daad.de` | 專屬 class（`src/crawler/sources/daad.js`，抓其靜態 TaffyDB 資料檔） | 163 |
| 東海大學獎助學金查詢 | 台灣・學校 | `fsis.thu.edu.tw/wwwstud/frontend/Scholarship.php` | 專屬 class（`src/crawler/sources/thu.js`） | 154，單頁無分頁 |
| European Funding Guide（歐洲獎學金資料庫） | 歐洲多國・民間資料庫 | `european-funding-guide.eu/scholarship/abroad` | 專屬 class（`src/crawler/sources/efg.js`，標準 Drupal Views table + `?page=N` GET 分頁） | 125（抓前 5 頁，實際共 91 頁） |

東海大學/EFG 來源選擇寫**專屬 class**而非 `GenericCrawler`：實測其 DOM 結構清楚穩定，
比對用 `GenericCrawler` 跑同一個頁面的診斷結果（見下方 DEBT 表），專屬 class 精準度明顯較高。

### 網址單一事實來源（single source of truth，本輪修復）

每個專屬 crawler class（`moe.js`/`daad.js`/`thu.js`/`efg.js`）過去在建構子裡各自**硬編碼**一份
自己的 `this.url`，跟 `config/sources.seed.json`/`data/sources.json` 儲存的網址是**兩處分別維護**
的值。這曾在 MOE 身上造成實際 bug：`MoeCrawler` 建構子寫死的是分類清單頁網址（人工瀏覽用），
跟 `sources.seed.json` 儲存的實際分類網址不同，導致 `sourceManager.updateSourceHealth()` 的完全
字串比對永遠找不到對應紀錄，MOE 的健康分數/`lastChecked` 從未真正更新過（爬取本身不受影響，
只有健康監控/自動停用安全網失效）。

**修復**：`createCrawler(source)`（`src/crawler/index.js`）現在把 `sources.seed.json` 儲存的
`source.url` 統一注入所有 4 個專屬 crawler 的建構子（`new MoeCrawler(source.url)` 等），4 個
class 的建構子都改成 `constructor(url) { super(name, url || 舊預設值) }`——只有在沒傳入 url
時（例如單元測試直接 `new MoeCrawler()`）才 fallback 到舊的硬編碼常數。這樣不管
`sources.seed.json` 未來怎麼改，crawler 回報的網址標籤永遠跟設定檔一致，不會再有兩處分別維護
同一個值卻忘記同步的風險。`updateSourceHealth()` 另外加了一層防呆：比對失敗時記錄警告到
`work/error.log`（含目前所有已知網址列表），作為未來若又有類似不一致時的安全網，不會再像這次
一樣靜默潛伏數月才被發現。

**Universal Principle**：任何「同一個概念性的值（例如網址）需要被程式碼跟設定檔分別讀到」的
情境，都應該讓其中一處成為唯一權威來源、另一處在執行期讀取/注入，而不是各自寫死一份、
依賴人工記得同步更新兩處。新增來源時，crawler 建構子應比照這個模式接受可注入的 url 參數。

### 「納入全世界所有獎學金網站」指令的範圍界定（重要，請詳讀）

使用者曾要求「納入全世界所有可用於申請獎學金的網站，不限於德國和台灣」。**按字面窮舉全世界
在技術上不可行**，也違反這個系統一路以來的 Low-Compute/Lightweight/單人維護原則：
- 來源數量沒有上限，代表維護成本（每個來源改版都要人工介入修 selector）也沒有上限
- 系統無法「自動發現」新的獎學金網站——這需要語意判斷式的網路搜尋/爬取，但硬性限制
  禁止引入 AI，純規則式系統做不到「自主判斷這是不是一個獎學金網站」

**本輪的實際作法**：不窮舉，改為依明確標準分批新增有代表性的來源。這輪**實測了 13 個國際候選來源**，
橫跨北美/英國&大英國協/亞太/國際組織四個地區類型，結果：

| 候選來源 | 地區類型 | 排除原因 |
|---|---|---|
| Fulbright Foreign Student Program | 北美 | 純研究所/研究學者專案，無單一集中清單頁（各國分館各自受理），會被年級閘門排空 |
| Killam Fellowships Program | 北美 | 單一旗艦專案介紹頁，不是多筆項目的清單頁 |
| Rotary Foundation Scholarships | 國際組織 | 同上，實際獎助金由各地分會各自受理，官網只有專案介紹 |
| Commonwealth Scholarship Commission | 英國/大英國協 | 明確僅碩士以上（Commonwealth Shared Scholarships 官方文字寫明「not for undergraduate」），會被年級閘門排空 |
| British Council (study-uk.britishcouncil.org) | 英國/大英國協 | 403 反爬蟲阻擋 |
| JASSO（日本學生支援機構） | 亞太 | 頁面只有 3 個大分類專案連結，不是多筆個別項目的清單 |
| MOE Singapore Awards & Scholarships | 亞太 | 可正常抓取，但清單內容幾乎全是新加坡本地學生的在學獎勵，僅有的國際項目（ASEAN Scholarship）限定東協籍，台灣籍申請人不適用 |
| Erasmus+ 官方站 | 國際組織 | 頁面只有 5 個大分類（留學/實習/聯合碩士等）說明，不是個別獎學金清單 |
| ScholarshipsPortal.eu | 國際組織 | 大量內容需要 JS 動態渲染，靜態 HTML 抓不到實質清單 |
| Study in Sweden | 歐洲 | 同上為 SPA，且清單只是導向 3 個外部網站的推薦連結，非自有資料庫 |
| European Funding Guide | 國際組織/歐洲多國 | ✅ **通過，已納入** |

**目前結果**：四項地區類型裡，僅「國際組織/歐洲多國」找到符合標準的候選（European Funding
Guide）。北美、英國/大英國協、亞太**目前沒有新增來源**，這是誠實的落差，不是省略未做。

**下一輪若要繼續擴充，建議的更有效率作法**：由使用者自行提供候選網址清單（例如已知某個
國家的官方獎學金入口網址），系統負責批次驗證（SCAN：DOM 結構、免登入、無反爬蟲）與新增，
取代目前這種「自主搜尋 + 逐一試錯」的方式——後者在本輪耗費大量來回測試但成功率偏低
（13 個候選裡只有 1 個通過），前者可以精準命中使用者已知可靠的來源。

### 「使用者主導、系統輔助驗證」的實作（本輪新增，`/驗證來源`）

上方建議的作法本輪已實作：使用者要求「爬蟲應可自動延伸擴充全世界各國公開獎學金網站來源」，
這句話字面上容易被理解成「系統自主探索新網站」，但**這仍然違反硬性限制**——自動探索需要
語意判斷「這個網址是不是獎學金公告列表頁」，等同需要 AI/語言偵測。**再次明確界定**：本輪做的
是使用者主導、系統輔助驗證，不是系統自主發現，兩者性質不同：

1. **使用者**丟一個候選網址給 `/驗證來源 網址`
2. **系統**自動執行三層檢查：連線可用性（含反爬蟲阻擋偵測，比照 HTTP 403/429）、
   SPA 偵測（靜態 HTML 內容過少的啟發式判斷，比照先前 ScholarshipsPortal.eu/Study
   in Sweden 遇到的情況）、`GenericCrawler` 保底邏輯實際抓取一次並回報筆數與範例標題
3. 驗證結果（通過或不通過＋具體理由）回報給使用者，**不會自動加入**
4. 使用者自己判斷範例標題是否合理，若接受，自行執行既有的 `/addsource` 指令才會正式加入

見 `src/crawler/sourceValidator.js`。測試涵蓋 1 個預期通過（重複驗證既有來源東海大學
獎助學金查詢，147 筆項目、479 個原始連結，通過率約 30.7%）與 1 個預期失敗（British
Council，確認正確偵測到 HTTP 403 反爬蟲阻擋）。

### 「引用連結擷取」（本輪新增，`/擷取引用連結`）——與 AI 自動收錄的邊界區分

使用者本輪再次要求「爬蟲應可逐步按照引用來源自動延伸擴充全世界公開獎學金網站」。
**判定範圍**（與使用者確認一致，未收到修正指示）：從既有已監測來源的頁面內容中，
用純 DOM/正則抽取頁面內出現的外部連結，整理成候選清單，交由既有的 `/驗證來源`
指令批次驗證，最終仍需使用者透過 `/addsource` 人工確認——**不做**「系統自動判斷
連結是否為獎學金網站並自動收錄」，那需要語意判斷，違反硬性限制。

**實作**（`src/crawler/linkExtractor.js`）：抓取指定來源的頁面 HTML，用 cheerio 找出
所有 `<a href>`，排除：
- 內部連結（**用「根網域」比對，不是只比對完全相同的 hostname**——實測東海大學
  來源時發現第一版只比對完全相同 hostname，會把同一個機構的其他子網域（例如
  `www.thu.edu.tw`、`bus.service.thu.edu.tw`）誤判成「候選外部連結」，實際上這些
  正是 `GenericCrawler` 的 `NAV_NOISE_WORDS` 黑名單已經歸納過的同一批雜訊（首頁/
  公車查詢等）。改用根網域比對後這 5 筆全部正確排除）
- 社群媒體網域（facebook/twitter/instagram/line/youtube 等）
- 廣告追蹤網域（google-analytics/doubleclick/googletagmanager 等）
- 已經在 `sources.seed.json`/`data/sources.json` 追蹤清單裡的網址

**實測結果**：東海大學獎助學金查詢頁面修復前找到 5 個「候選連結」（實際全是同機構
雜訊，修復後正確變成 0 個）；DAAD 找到 0 個（其列表頁是 JS 動態渲染的 SPA，cheerio
本來就抓不到，屬既有已知限制）；European Funding Guide 找到 3 個真實候選（歐盟教育
執行機構、Ashoka 社會企業基金會、startsocial.de 德國社會企業扶植計畫的連結，皆為
合理的機構型引用連結）。

## 安裝與執行

```bash
npm install
cp .env.example .env   # 填入 Discord Bot Token 與你的 Discord 使用者ID
npm run run:once       # 手動測試單次爬取（跑完立即結束 process）
npm start               # 常駐模式：Discord Bot + node-cron 排程（6h 爬取 / 日報 / 週報 / 8h 補跑）
npm run report:daily    # 手動觸發一次日報（獨立 process，測試用）
npm run report:weekly   # 手動觸發一次週報
```

或用 Docker：

```bash
docker compose up -d --build
```

## 部署前必須處理的債務（P1 = 阻擋上線）

| 優先度 | 項目 | 影響 |
|---|---|---|
| P2（本輪新發現） | `rules.json` 的 `phd`(3分)/`master`(2分) 計分權重跟六項條件的「年級」閘門互相矛盾 | 這兩個權重隱含系統原本設計是給研究所申請者用；現在年級閘門會在計分前就把 PhD/Master 限定的項目排除掉，權重形同虛設。不在本輪範圍內調整，需要使用者決定是否移除/改權重 |
| P3 | `exclude_keywords` 沒有额外的科系排他句型（限工程/限商管/限法律/限醫護等） | **實測驗證**：抽樣 332 筆真實資料找不到任何一筆這類句型，故未擴充（不得憑空列舉未實際出現過的詞）；`限工程學系` 這類假設性測試標題目前**不會**被排除，屬已知、刻意的範圍限制 |
| P3 | `rules.json` 中文關鍵字仍有 45 筆殘留覆蓋缺口（純機構/廟宇專有名稱） | 例如「行天宮」「許姓宗祠」，標題本身不含任何通用獎學金字樣，純關鍵字比對本來就無法涵蓋，屬預期限制而非 bug。**本輪重新驗證數字不變**（MOE+THU 169 筆，被硬性排除筆數從 28→32，純覆蓋不足仍是 45，兩個桶子彼此獨立） |
| P2（本輪已修復） | `isExcludedBySchool()` 漏放「限本校（校名）」括號句型、簡稱列舉（僅限台大、政大）、「在校生」同義詞 | **根因**：舊版 regex 要求「限」緊接完整校名（含「大學/學院」尾綴），三個真實變體都不符合這個嚴格形狀。已修復並確認 457 筆真實資料排除筆數維持 0 筆不變（純新增能力） |
| P2（本輪已修復） | `isExcludedByNationality()` 未涵蓋「原住民」身份限定 | 新增 `isExcludedBySpecialStatus()`（含原住民+僑生，僑生從國籍規則移過來歸類）。**實測**：MOE+THU 169 筆重新驗證，通過筆數 105→101（4 筆原住民限定項目正確排除，之前被誤放行） |
| P1（本輪新發現+已修復） | `isExcludedByNationality()` 既有的隱藏 bug：「限OO市/縣**設籍**」會被誤判成國籍限定 | **根因**：`限([一-龥]{2,6})(?:國籍|籍)` 的「籍」分支貪婪比對加回溯，會把「限台中市設籍」誤擷取成「台中市設」+「籍」，跟真正的國籍限定（如「限日本籍」）混淆——這是本輪重構前的既有邏輯就有的 bug，不是本次字典重構造成的（用重構前的原始 regex 直接測試可重現），本輪做多語言字典的回歸測試時才發現。**已修復**：加上「國名不含市/縣/設/戶」的特徵過濾，重新測試確認使用者自己的城市（台中市）不再被誤殺，且真正的國籍限定（限日本籍/限美國籍）維持正確排除。457 筆真實語料裡沒有任何一筆包含「設籍/戶籍」字樣，故此 bug 對本輪之前各輪回報的通過筆數沒有實際影響，純粹是防禦性修復 |
| P3（已修復） | European Funding Guide 來源曾實測 0/125 筆通過六項閘門+計分門檻 | **已修復**：實測 125 筆真實標題歸納出 `grant`/`grants`（31 次，遠高於中文那批的最低取用頻率）與 `stipendium`（德文「獎學金」，3 次，核心詞比照獎助學金給權重 2）並加入 `rules.json`；`bourse`/`aide`/`beca`（法文/西文同義詞）在這批樣本裡出現 0 次、`fellowship`/`award`/`stipendien` 只出現 1 次（低於既有取用門檻），故未加入，不得憑空列舉。通過筆數 0 → 33（見下方「計分權重」段落） |
| P2（本輪已改善） | `GenericCrawler`（給 Discord `/addsource` 動態新增網址用）用啟發式抓取，沒有專屬 selector | **實測驗證**：對東海大學獎學金頁面跑 `GenericCrawler` 診斷，加入導覽詞黑名單（查詢/首頁/公車/課程等，皆從實測雜訊歸納、對 154 筆真實資料 0 誤判）+ 去重後，雜訊率從 10.9%（18/165）降到 **0%（0/147）**；但總筆數也從 165 降到 147，少的 7 筆是既有的「標題長度 < 6 字」門檻篩掉的真實項目（例如「白曉燕文教」5 字），非本輪改動造成，屬既有的精準度/召回率取捨 |
| P2 | MOE 的 GridView 分頁邏輯（postback 跟隨）**未經真實驗證** | 目前「民間團體獎助學金」（15 筆）與「政府機關獎助學金」（0 筆）兩個分類都只有一頁、沒有渲染分頁列，找不到任何目前有兩頁以上資料的分類可以實測；分頁程式碼已依 ASP.NET GridView 標準慣例寫好（含終止條件與延遲），但第一次真的被觸發時必須人工核對結果 |
| P3（本輪部分修復） | 沒有日期擷取，`publishDate` 固定為 `null` | **部分修復**：新增 `src/crawler/detailParser.js` 對詳情頁擷取「截止日期」（見「訊息四欄位格式」章節），MOE/THU 兩個來源可靠轉換成 `YYYY-MM-DD`；DAAD/EFG 常是自由文字或無年份格式，找不到具體日期時顯示「未提供」，不臆測。`db.js` 的 `publishDate` 欄位本身仍固定為 `null`（crawler 列表頁沒有這個欄位），新的 `deadline` 是獨立欄位，兩者不要混淆 |
| P3（本輪新發現） | EFG 詳情頁沒有穩定的 `conditions`/`applicationMethod` 專屬 DOM 欄位 | 內容混在自由格式的說明文字裡（不同投稿者格式不一致），無法可靠擷取，這兩個欄位在 EFG 來源恆為 `null`（渲染成「未提供」），屬資料來源本身的限制，非實作疏漏 |
| P3（本輪新發現） | `special_status.disability_illness` 的「癌症」關鍵字有贊助機構名稱誤判風險 | 目前只有 1 筆真實樣本（已用詳情頁驗證過是真的疾病限定），跟先前 nationality 規則踩過的「German National Academic Foundation」同一種風險類型——若未來出現「XX癌症基金會贊助但無疾病限定」的反例會誤判，需要重新檢視 |
| P2 | 詳情頁擷取讓爬取耗時隨「本次新增且通過六項閘門的項目數」增加（每筆 1 秒禮貌延遲） | 目前歷史紀錄每輪新增筆數多在個位數~一百出頭，換算下來可能增加數十秒到數分鐘，仍在 6 小時排程週期內可接受，但未經過大量新增項目（例如新增一個高流量來源）情境下的實測，需要之後留意 |
| P3 | `Dockerfile`/`docker-compose.yml` 的清理**未經實際 `docker build` 驗證** | 本機 Docker daemon 未啟動（`docker build` 直接回報連不上 daemon），只做過人工逐行檢查，語法上沒有明顯問題，但沒有真的 build 成功過 |
| P1（本輪已修復） | 「全指令失效」事件：根因是 Bot process 從未真正以 `npm start`（持久模式）執行過，只跑過 `npm run run:once`（跑完即 `process.exit(0)`），沒有存活的 process 監聽 `messageCreate`，所有指令自然都收不到回應 | **不是任何指令的 regex 壞掉**——`handleMessage()` 直接單元測試 11 個指令（含 `/help`）全數正確回應，證實指令邏輯完全正常。已修復：實際執行 `npm start` 並確認進入 `🟢 BOT READY` 狀態；`docs/RUNBOOK.md` Phase 4.5 新增明顯警告與健康檢查指令（`tasklist /FI "IMAGENAME eq node.exe"`），避免同樣情境再發生 |
| P2（本輪新發現） | 目前的持久化 process 是透過終端機手動啟動、無 process supervisor（例如 pm2、Windows 服務、或有 restart policy 的 Docker container） | 若 process 意外崩潰或環境重啟，不會自動復原，需要人工重新執行 `npm start`，可能重演這次「全指令失效」的情境；建議之後改用 Docker（`docker compose up -d`，已有 restart policy）或 pm2 常駐 |
| P2（本輪已修復） | `/獎學金說明` 只接受純數字，使用者實測輸入 `[11]`、`11.`、`11,` 皆完全無回應 | **根因**：舊 regex `/^\/獎學金說明 (\d+)$/` 過嚴，且說明文字用中括號當佔位符（"[編號]"）容易被使用者誤解成實際語法。已修復：容錯解析（去除中括號/句點/逗號/空白後再解析數字），解析失敗時明確回覆錯誤訊息＋正確範例而非靜默忽略；`/查詢獎學金`、`/篩選獎學金` 也做了同樣的中括號容錯。見「截止日過濾規則」上方的指令表 |
| P2（本輪新發現，已部分緩解） | 截止日過濾規則（距今需≥1週）目前對現有資料**沒有實際篩選作用** | **SCAN 實測**：`data/db.json` 140 筆真實記錄 `deadline` 有效率是 0%（0/140），規則已正確實作並通過合成資料測試（3 組情境全數符合預期），但要等詳情頁擷取真的抓到有效 `deadline` 值的新項目才會實際發揮效果，目前形同「已就緒但未觸發」 |
| P2（本輪已修復） | `/查詢獎學金`、`/篩選獎學金` 硬性只顯示前 20 筆，其餘筆數被砍掉不顯示 | **根因**：舊版 `.slice(0, 20)` 是人為硬性上限，不是 Discord 限制。已修復：新增 `chunkLines()` 依 2000 字元上限自動分成多則訊息，實測 80 筆長標題假資料正確分成 3 則訊息、全部項目皆送出無遺漏 |
| P3（本輪已修復） | `/獎學金說明` 對 `deadline=null` 的項目會顯示兩行意思重複的「未提供」訊息 | 已整併成同一行（`未提供（⚠️ 請自行至原始連結確認）`），資訊不變但不再重複 |
| P3（本輪已確認非 bug） | 使用者回報 `/獎學金說明` 輸出裡「連結：」後面疑似空白 | **SCAN 實測**：`data/db.json` 140 筆記錄 `link` 欄位 0 筆為空，`fmtItem()` 樣板也正確接在 `連結：` 後面輸出網址，複測沒有重現空白的情況——研判是 Discord 客戶端複製貼上時的顯示/預覽artifact，非資料管線 bug |
| P3（本輪新增） | `/驗證來源` 的 SPA 偵測、GenericCrawler 抓取診斷都是啟發式判斷，非 100% 準確 | 沿用既有 `GenericCrawler` 本身「沒有專屬 selector、低信心保底方案」的既有限制，回覆訊息已明確提醒使用者仍需自行判斷範例標題是否合理，不會自動加入 |
| P2（本輪第二次複查，仍未實作） | Gemini API 輔助來源發現功能**尚未實作**（`src/sourceDiscoveryAI.js` 尚不存在） | 使用者已明確授權針對「候選來源合法性判斷」單一用途解除 AI 限制。**第二次複查**：`.env` 裡的 key 前綴/長度跟上一輪記錄的完全相同（研判尚未換成新 Google Cloud 專案綁定的 key），`ListModels` 仍成功（身份驗證有效），`generateContent` 仍回報 429，quotaId 明確帶 `-FreeTier` 字樣、`limit: 0`。詳細下一步檢查清單（換新 key/連結計費帳戶/啟用 Generative Language API/查看配額頁面）已記錄於 `Meta_Dev_Knowledge.md`，需使用者實際操作後才能繼續。範圍界定：僅限來源合法性判斷，不影響六項排除閘門/計分邏輯，AI 判斷失敗一律降級回純規則式驗證（`sourceValidator.js`） |
| P2（已修復） | 幾乎所有指令對「格式不完整/不符合語法」的輸入曾是**完全無回應** | **已修復**：新增 `matchOrUsageError()` helper，統一套用到 `/update`、`/rule`、`/排除關鍵字新增`、`/排除關鍵字移除`、`/驗證來源`、`/addsource`、`/removesource`、`/擷取引用連結`、`/查詢獎學金`、`/篩選獎學金`、`/依地區查詢`、`/獎學金說明` 共 12 個帶參數指令——只要訊息看起來是要打這個指令（指令名稱+空白或到此為止，避免 `/rule` 誤判 `/rules`）但格式不符，一律回覆明確用法提示。實測 13 組先前無回應的案例全數改為明確錯誤訊息，且全部合法輸入的既有行為維持不變（重新測試 16 個指令的正常呼叫，皆正確） |
| P2（已修復） | `/篩選獎學金` 對「類別名稱本身含空白」（例如 `European Funding Guide（歐洲）`）曾完全無回應 | **已修復**：regex 從 `\S+` 改為 `.+`（沿用 `/擷取引用連結` 已驗證過的修法），實測 `/篩選獎學金 European Funding Guide（歐洲）` 正確回傳 28 筆 |
| P2（本輪已修復） | **MOE 的來源健康分數/`lastChecked` 從未真正更新過**（上輪結案盤點發現） | **根因**：`MoeCrawler` 建構子硬編碼 `this.url` 為 `.../Content_List.aspx?n=D50A7AEB3C165858`（分類清單頁），跟 `sources.seed.json` 儲存的 MOE 網址 `.../Grants.aspx?n=2BBF7170197CE7D3&sms=...` 完全不同，導致 `updateSourceHealth()` 的完全字串比對永遠找不到對應紀錄，健康分數/`lastChecked` 靜默凍結。**修復（Option B + C 組合）**：(1) `createCrawler(source)` 改為把 `sources.seed.json` 的 `source.url` 注入所有 4 個專屬 crawler（`moe.js`/`daad.js`/`thu.js`/`efg.js` 建構子都改成 `constructor(url) { super(name, url || 舊預設值) }`），確保單一事實來源，不只修 MOE 一處，同時排除 DAAD/THU/EFG 未來重演同類問題的風險；(2) `updateSourceHealth()` 找不到對應紀錄時新增 `logger.error()` 防呆日誌（Option C），作為第二層安全網。**實測驗證**：修復前 MOE `lastChecked` 凍結在 `2026-07-03T16:55:51.766Z`；重新觸發 `crawlAll()` 後變為 `2026-07-04T17:41:03.739Z`，證實 MOE 健康記錄已能正確被找到並更新。**回歸測試**：再次執行 `crawlAll()`，DAAD/THU/EFG 三者的 `lastChecked` 同步從 `17:41:03` 前進到 `20:40:57`，分數維持不變（DAAD/THU/EFG 皆為 10），確認本次修復未改變其既有行為。**防呆機制驗證**：模擬呼叫 `updateSourceHealth('不存在的網址', [])`，確認 `work/error.log` 新增一行含 `knownUrls`（4 個合法來源網址列表）的警告紀錄 |
| P2（本輪已修復） | 其他 3 個來源（DAAD/THU/EFG）目前網址一致，但架構本身容易在未來重演同類「crawler 硬編碼網址 vs 設定檔網址分離維護」問題 | **已用 single source of truth 從源頭解決**：見上方「已修復」條目，`createCrawler()` 統一注入 `source.url`，4 個來源皆不再各自硬編碼；`updateSourceHealth()` 加上防呆日誌作為第二層安全網 |
| P2（本輪四維度體檢發現並修復） | 全形英數字標題會讓計分/排除規則靜默完全失效（例如全形「Ｇｅｒｍａｎｙ」比對不到半形 `rules.json` 關鍵字「germany」） | **已修復**：新增 `normalizeTitle()`（`String.normalize("NFKC")`），在 `scoreItem()`/`filter()` 讀取 title 的源頭套用，下游所有排除函式共用同一份已正規化的標題。實測半形/全形同句話修復前 score 5→0（全形被錯誤忽略），修復後皆為 5。真實 140 筆語料 0 筆含全形字元，回歸測試確認通過筆數修復前後皆為 135，無行為改變（僅在目前未觸發的全形情境下修正邏輯缺陷） |
| P2（本輪已修復） | `config/exclude_terms.json` 的 `school_restricted.name_suffix.zh` 有未提交的工作副本變更，原本的通用尾綴詞 `["大學","學院"]` 被整個取代成 11 項具體獎學金/科系/身份專有名詞，導致 `isExcludedBySchool()` 對所有「限OO大學/OO學院在學學生」句型排除規則失效 | **已修復（方案 C：分類歸位）**：`school_restricted.name_suffix.zh` 恢復為 `["大學","學院"]`（句型辨識用的通用尾綴詞，非直接排除觸發詞——見下方「排除邏輯分工」章節）。11 項逐一分類：「原住民」移回既有的 `special_status.indigenous`（純刪除重複，該分類本來就正常運作）；「新住民」移至新增的 `special_status.new_resident`（比照 indigenous/overseas_student/refugee 模式，新增 `profile.is_new_resident` 欄位）；「體育」「美術系」「國貿系」與從「會計系羅黃葉勤學敦品獎助學金」抽出的「會計系」共 4 個科系關鍵字，移至 `profile.json` 既有的 `exclude_keywords`（科系不符排除機制，見 `_comment_major_aliases`）；「中國工程師學會-莫衡先生紀念獎學金」「全國台南一中校友總會-母校畢業校友現就讀各大學系所獎助學金」這兩個不屬於學校/科系/身份別任何一類的完整獎學金名稱，同樣移至 `exclude_keywords` 做個案排除；「新應材新住民子女獎學金計畫」「新住民及其子女培力與獎助學金」「東海大學原住民學生優秀獎學金」這三個完整標題已被抽出的短詞（新住民/原住民）涵蓋，屬多餘重複，不再保留。**測試驗證**：「限國立臺灣大學在學學生獎學金」正確排除（他校校名）；「大學部獎學金開放申請」正確通過（泛稱詞不誤判）；「東海大學專屬獎學金」正確通過（本校）；「限原住民學生獎助學金」正確排除（身份別移回後仍正常）；「美術系吳學讓國畫獎學金」正確排除（科系限制生效）；「新住民及其子女培力與獎助學金」正確排除（新住民身份別新增後正常）。真實 140 筆語料回歸測試：THU 從 85→71（14 筆新排除，全數對應到本次新增的科系/個案/新住民排除，逐筆核對無誤判），MOE/DAAD/EFG 不變（6/16/28），無非預期行為改變 |

## 目錄結構

```
scholarship-monitor/
├── src/
│   ├── index.js              # 入口點：啟動 Discord Bot，--once 跑單次，否則進 scheduler 常駐模式
│   ├── scheduler.js          # node-cron 排程 + 8 小時補跑 + 健康告警
│   ├── runLog.js             # 每次爬取的來源健康歷史（data/runs.json）
│   ├── ruleEngine.js         # 資格判斷（關鍵字權重 + 六項硬性排除規則，比對詞彙讀 config/exclude_terms.json）
│   ├── db.js                 # JSON 檔案儲存：已通知過的獎學金記錄（含 eligibility/conditions/applicationMethod/deadline 四欄位）
│   ├── sourceManager.js      # 來源清單 CRUD + 健康分數（data/sources.json）
│   ├── sourceAI.js           # 來源健康度打分（非獎學金項目 AI 判讀）
│   ├── logger.js             # activity.log / error.log 分離
│   ├── crawler/
│   │   ├── base.js           # BaseCrawler/GenericCrawler：抓取、重試、正規化
│   │   ├── index.js          # createCrawler() 依網址對應專屬 parser、crawlAll() pipeline（含 detailParser 詳情頁擷取）
│   │   ├── detailParser.js   # 對通過六項閘門的存活項目擷取詳情頁，解析四欄位（見「訊息四欄位格式」章節）
│   │   ├── sourceValidator.js # /驗證來源 指令背後的批次驗證邏輯（連線/反爬蟲/SPA/GenericCrawler診斷）
│   │   ├── linkExtractor.js  # /擷取引用連結 指令背後的連結抽取邏輯（純 DOM/正則，非語意判斷）
│   │   ├── runner.js         # mutex，避免同時執行兩次
│   │   └── sources/          # 各來源實作（daad.js, moe.js, thu.js, efg.js）
│   ├── discordBot.js         # 統一 Discord Bot：推播 + 互動指令（雙向，含中文查詢/篩選/說明/排除關鍵字管理/來源驗證/連結擷取/地區查詢指令）
│   └── report/
│       ├── format.js         # fmtItem/sourceDisplayName/groupByCategory/renderGrouped 共用格式化邏輯
│       └── generateReport.js # 日報/週報組裝
├── config/
│   ├── profile.json          # 個人條件（含 exclude_keywords、is_indigenous/is_overseas_student/is_refugee/is_disability_or_illness）
│   ├── rules.json            # 關鍵字權重與 threshold
│   ├── exclude_terms.json    # 六項硬性排除規則的多語言詞彙字典（見「多語言排除字典」章節）
│   └── sources.seed.json     # 來源初始清單（靜態、進 git），首次啟動時 seed 進 data/sources.json
├── work/                     # log 輸出（gitignored）
├── data/                     # db.json / sources.json / runs.json（皆 gitignored，執行期產生/變動）
├── reports/                  # /reports/Status_{YYYYMMDD}.md 現況報告存檔（進 git，視為文件產出非執行期產物）
├── docs/RUNBOOK.md           # 操作手冊（含 GitHub 異地備份流程，見 6.5a）
├── Meta_Dev_Knowledge.md     # 跨輪次持續生效的治理流程規則（例如 POST 步驟的 REMOTE 欄位規範）
├── docker-compose.yml
└── Dockerfile
```
