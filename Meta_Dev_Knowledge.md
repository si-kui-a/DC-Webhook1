# Meta_Dev_Knowledge.md
## intel-pusher 開發知識庫

本檔案為本專案首次治理紀錄（2026-07-05 Phase 3 上線驗證時建立）。

---

### [PAT-01] certifi bundle 對特定政府網站憑證鏈驗證過嚴（cbc.gov.tw）

**症狀**：`requests.get("https://www.cbc.gov.tw/tw/rss-302-1.xml")` 拋出
`SSLCertVerificationError: certificate verify failed: Missing Subject Key
Identifier`，但 `openssl s_client -connect www.cbc.gov.tw:443` 與 Windows
系統信任庫都回報 `Verify return code: 0 (ok)`。

**根因**：
- cbc.gov.tw 使用 TWCA（台灣網路認證公司）簽發的憑證鏈，其中一張憑證缺少
  Subject Key Identifier 擴展欄位。
- Python `requests` 預設用 `certifi` 套件內建的 CA 清單驗證，不是作業系統
  的信任庫；certifi 的驗證邏輯對這個缺陷比較嚴格，判定失敗。
- Windows 系統信任庫（及 openssl CLI 用的信任庫）對同一張憑證判定通過，
  代表這不是憑證真的無效，是 certifi 的驗證嚴格度問題。

**修復規則**：
> 改用 `truststore` 套件（PyPA 維護），在程式最前面呼叫
> `truststore.inject_into_ssl()`，讓 Python 的 `ssl` 模組改用作業系統原生
> 信任庫做驗證，而不是 certifi 內建清單。已加進 `requirements.txt` 與
> `main.py` 開頭（`import truststore; truststore.inject_into_ssl()`，需在
> `import requests`/其他會建立 SSL context 的模組之前執行）。

**驗證**：加入 `truststore.inject_into_ssl()` 後，`cbc.fetch()` 正常回傳，
筆數與內容和直接 curl 測試一致。

---

### [PAT-02] pr.tsmc.com 中文路徑被 Cloudflare bot 防護擋下，英文路徑正常

**症狀**：`pr.tsmc.com/chinese/events/investor-meetings` 與
`pr.tsmc.com/chinese/latest-news` 用 `requests` 存取回傳 403，回應標頭含
`Cf-Mitigated: challenge`。同網域下的 `pr.tsmc.com/english/latest-news`
用同一支程式、同一個 User-Agent 存取則正常回傳 200（`cf-cache-status:
HIT`）。

**根因**：Cloudflare 的 bot 防護規則對這兩個路徑的設定不同（可能是中文站
特別啟用了較嚴格的 JS challenge），不是 UA 或 requests 本身的問題——換 UA
測試過，中文路徑一樣被擋。

**修復規則**：
> `scrapers/tsmc.py` 改用 `pr.tsmc.com/english/latest-news` 作為資料來源，
> 範圍從原本設定的「法說會活動」調整為「英文版新聞稿全清單」（含月營收
> 報告、法說會公告等，涵蓋範圍其實更廣）。若之後真的需要中文內容，必須
> 先假設會撞到同樣的 Cloudflare challenge，不要當作單純的 selector 問題
> 去除錯。

---

### [PAT-03] 「一次回傳全部歷史」的來源需要首次執行安全閘門，且不能用 'new' 狀態承擔雙重語意

**情境**：cbc 的 RSS feed 沒有日期篩選，`fetch()` 一次回傳約 500 筆歷史
資料。資料庫是空的情況下，若直接把 500 筆全部當「新項目」推播，會洗版
Discord 頻道。

**解法**：
1. `main.py` 的 `FIRST_RUN_PUSH_CAP = {"cbc": 5}`：只有第一次執行
   （`db.count_items_for_source(source_id) == 0`）且該來源有設定 cap 時才生
   效。fed/tsmc 本來就是個位數筆數，不套用這個閘門。
2. 閘門邏輯：全部 500 筆都寫入 `item` 表建立 `dedup_key`（保留完整歷史
   記錄），但只有最新 N 筆（RSS 本身是新到舊排序，取前 N 筆）真的呼叫
   webhook 推播。

**status 欄位的雙重語意風險（本次實際發生過的設計決策）**：
- 一開始閘門邏輯讓「存檔不推播」的項目 status 維持預設值 `'new'`，跟
  「真正尚未處理過、之後應該被推播」的項目共用同一個狀態值。
- 風險：未來如果加上 catch-up job 或手動 replay 指令，很自然會寫成
  `SELECT * FROM item WHERE status='new'`，這樣會把這 495 筆歷史存檔誤判
  成待推播，一次噴發到 Discord。
- **最終決定**：改用專屬的 `status='seeded_historical'`（見 `db.py` 的
  `mark_seeded_historical()`、`schema.sql` 的 status 欄位註解），不是新增
  一個額外的布林欄位（如 `is_first_run_seed`）+ 要求未來的查詢記得多加一個
  WHERE 條件。理由：字串狀態值本身就是自我說明的——只要有人寫
  `WHERE status='new'`，這批歷史資料就自然被排除，不需要仰賴「記得排除
  某個 flag」這種容易被遺忘的人為紀律；而且不用改 schema 結構（不用新增
  欄位、不用 migration），純粹是同一個 TEXT 欄位多一個合法值。
- 已對既有 `data.db` 做過一次性回填：495 筆從 `'new'` 改成
  `'seeded_historical'`（用「不在 delivery_log 裡」作為安全過濾條件，
  避免誤改到真正推播過的 5 筆）。

**強制規則**：
> 任何「一次回傳全部歷史」的新來源，都要重複這個模式：閘門邏輯裡「存檔
> 不推播」的項目一律標記 `seeded_historical`，不要留在預設的 `'new'`。

---

### [PAT-04] build_embed() 原本沒有 published_at，同標題不同會議無法區分【CORE_IMMUTABLE】

**症狀**：fed 來源有 4 則標題完全相同的「Federal Reserve issues FOMC
statement」（分屬 1/28、3/18、4/29、6/17 四場不同會議），但推播到 Discord
的 embed 完全沒有日期欄位，人眼在頻道裡無法分辨是哪一次會議的聲明。

**根因**：`push_webhook.py` 的 `build_embed()` 簽章只有
`title/description/url/color/fields/footer`，沒有 `published_at` 參數；
`main.py` 呼叫時雖然 `item["published_at"]` 早就從 scraper 抓到並存進
`data.db`，但從未傳進 `build_embed()`，等於資料庫有日期、Discord 訊息卻沒有。

**修復**：
- `build_embed()` 新增 `published_at` 參數，附加在 `footer` 文字後面
  （例：`"美國聯準會-FOMC新聞稿 · 6/17/2026"`）。刻意不解析成統一日期格式
  直接顯示原始字串——各來源格式不同（fed `"6/17/2026"`、cbc RFC822、tsmc
  `"2026/06/10"`），統一格式化的解析成本與出錯風險，換來的價值有限，
  只求「看得到、對得上原文」就足夠解決「無法分辨」的問題。
- `main.py` 呼叫 `build_embed()` 時補上 `published_at=item.get("published_at")`。
- 已用模擬 embed（未真的呼叫 webhook）驗證：4 則同標題項目的 footer
  分別顯示 `1/28/2026`／`3/18/2026`／`4/29/2026`／`6/17/2026`，可正確區分。

**對已推播的 23 則舊訊息（7 fed + 5 cbc + 11 tsmc）的處置**：
> 維持原樣，不做事後編輯或刪除。這是修復前的一次性已知缺陷（bug 存在
> 期間推播的訊息沒有日期），修復後的推播自然會帶日期；不建立訊息
> 編輯/回溯機制去處理歷史訊息，因為 dedup 機制不會重新推播這 23 則，
> 額外做編輯功能的維護成本大於「23 則舊訊息缺日期」這個一次性缺陷本身
> 的影響。

**強制規則（CORE_IMMUTABLE）**：
> 任何來源、任何呼叫 `build_embed()` 的地方，只要該來源的 item 有
> `published_at` 欄位，就必須把它傳進去。新增來源時若漏了這個參數，
> 視為回歸（regression），比照本次 PAT-04 的修復方式處理。

---

### [PAT-05] commit c303cee 洩漏事故：現狀維持決定是有前提的，不是永久生效

**背景**：commit `c303cee` 內含 4 組真實 Discord Webhook URL（見
Meta_User_Feedback.md 2026-07-05 條目的完整根因記錄）。使用者於
2026-07-05 明確決定：倉庫（`DC-Webhook1`）為私有、不輪替這 4 組
webhook、不改寫 git 歷史移除該 commit。

**這個決定的前提，不是無條件成立**：
> 上述「維持現狀」的決定，前提是 `DC-Webhook1` 倉庫永久保持私有、
> 不新增協作者。若未來任何時候發生以下任一情況：
> (a) 這個倉庫的可見性被改為 public，或
> (b) 有新協作者被加入這個倉庫，或
> (c) 這 4 個 Discord 頻道的用途改變、Webhook 需要重新配置，
>
> 都應該回頭處理這個歷史洩漏——屆時「輪替這 4 組 webhook」就是必要動作，
> 不再是選項。不能假設 2026-07-05 這個決定永久有效；任何後續 session
> 在觸碰到倉庫可見性設定、協作者管理、或這 4 個 webhook 本身的變更請求
> 時，都要先重新檢查這個前提是否還成立，而不是預設沿用舊決定。

本條目純屬文件記錄，未觸發任何程式碼變更或立即行動。

---

### [PAT-06] 樣板文字一律在清洗階段用 regex 排除，不修改第三方 NLP 套件的內部行為

**背景**：Phase 7 本地摘要功能實作時遇到兩個樣板文字問題：
- cbc RSS description 清成純文字後，開頭常是「機構名稱 + 發布日期 +
  網址 + 選填公告編號」黏在一起、中間沒有標點的一段，被 TextRank 的
  分句器當成單一句子，還常常因為關鍵字密度高被排到摘要最前面。
- tsmc 詳情頁正文開頭固定是「HSINCHU, Taiwan, R.O.C., <日期> –」這種
  dateline，nltk 的 Punkt 分詞器不認得「R.O.C.」這個縮寫，會把它誤判成
  句尾，選出「HSINCHU, Taiwan, R.O.C....」這種近乎無用的短句當摘要。

**曾經考慮但排除的做法**：修改 nltk 的 `abbrev_types`（加入 R.O.C. 等
縮寫，讓 Punkt 分詞器認得）。排除原因：這是在修改/依賴第三方套件的
內部訓練參數，套件版本更新時這個 monkey-patch 可能失效或行為改變，
變成一個隱性、容易被遺忘的維護負擔。

**最終原則**：
> 樣板文字（機構樣板開頭、新聞稿 dateline 等）一律在「清洗階段」
> （scraper 的 fetch_detail_text() / summarizer 的 clean 函式）用 regex
> 主動移除，不要透過修改 nltk/textrank4zh/jieba 等第三方 NLP 套件的
> 內部訓練資料或參數去讓套件「自己認得」這些樣板。清洗規則寫在自己的
> 程式碼裡，版本可控、可測試、不會因套件升級而悄悄失效。

**實作模式（cbc.py + summarizer_zh.py / tsmc.py + summarizer_en.py 共用）**：
1. 先抓實際樣本（cbc 抓了 5 筆、tsmc 抓了 6 筆），找出真正重複出現的
   固定格式，寫成 regex，在丟進摘要套件之前（tsmc 是 `fetch_detail_text()`
   抓到正文之後；cbc 是 `clean_html()` 之後）就先移除。
2. 若已知確切文字內容（例如 cbc 的 RSS 標題本身在內文開頭重複一次），
   直接用該筆資料自己的欄位做精確比對移除，不要用猜的正則。
3. 樣板格式本身有變化、難以窮舉時（例如 cbc 有兩種機構名稱、「網址」
   後面冒號有全形/半形/無冒號三種），regex 要涵蓋已觀察到的變化，
   但同時準備一個更通用、不依賴窮舉的兜底規則（cbc 用的是「候選句子
   完全不含 。！？ 的視為疑似樣板，優先排除」）。
4. 任何以後新增來源遇到類似「分句被樣板污染」的問題，預設就套用這個
   三層模式，不需要每次重新討論要不要修改第三方套件內部行為——答案
   固定是不要。

**已知殘留現象（評估過，判斷不值得修，之後想「優化摘要品質」前先看這裡，
不要重複踩一次同樣的決策過程）**：

- **抽取式摘要的結構盲點**：cbc 某些新聞稿內文用「一、二、（一）（二）」
  這種平行子項編號分成兩個以上的並列小節（例如「依直接交易對手基礎」
  與「依保證人基礎」兩節，各自都有自己的（一）（二））。TextRank 是
  逐句獨立評分，不理解「這句話的（二）屬於哪個上層小節」這種段落
  結構，可能同時選中兩節裡編號相同、但內容不同的句子，讀起來像是
  重複編號、語意上略顯突兀（實例：115年3月底本國銀行國家風險統計，
  兩個「（二）」分別列出不同的國家排名清單）。這是抽取式摘要
  （相對於生成式摘要）的固有限制——它只能整句挑選，不能重寫或補上
  被跳過的上層脈絡。要解決得額外做段落/章節結構解析（先切出「一、
  二、...」與「（一）（二）...」的階層關係，摘要時整節一起取捨），
  這是明顯更大的工程量，且本專案摘要的目的只是給人一個提示、真正
  內容還是要點連結看全文，投入產出比不划算，故意不做。
- **textrank4zh 斷句限制**：某些句子（例如 cbc 人事異動稿裡帶方括號的
  警語「【本行為遵守節約簡樸，懇辭各界花籃及賀禮，並此聲明。】」）
  textrank4zh 自己的分句器選出來的句子邊界，可能在標點符號前就切斷
  （已確認不是本專案 MAX_LENGTH 截斷造成的，因為長度都在上限之內）。
  這是 textrank4zh 套件本身斷句規則的行為，不是本專案程式碼的 bug，
  故意不 patch 這個第三方套件的斷句邏輯（理由同 PAT-06 主文：不修改
  第三方套件內部行為）。

---

### [PAT-07] 多來源合併 pipeline：便宜檢查須先於昂貴運算
**規則**：dedup/日期過濾必須先做，summarize/detail頁請求只對確認新增的
項目才做。cbc_digest 曾因反序（先摘要後判斷）卡 7 分鐘（對 500 筆歷史
逐一算摘要才發現早已抓過）。

### [PAT-08] 跨頻道共用來源，去重鍵依頻道拆分，不可共用
**規則**：同一文章要給多頻道各自角度處理時，source_id 依頻道加後綴
（如 `princetonchen.crypto`／`.macrotech`）。共用一個 source_id，先處理
的頻道會標記掉 dedup_key，其他頻道永遠抓不到同一篇。

### [PAT-09] Discord 與 Telegram 的 Markdown 語法不通用
**規則**：Discord/CommonMark 用 `**粗體**`，Telegram 舊版 Markdown 只認
`*粗體*`。共用文字須經轉換（見 `notify_telegram._to_telegram_markdown`），
不可直接搬用，否則星號原樣顯示、排版糊在一起。

### [PAT-10] 「即時抓取」≠ 資料本身逐日變動
**規則**：抓取頻率與資料實際發布週期是兩件事（WALCL/TGA 為 FRED 週頻，
每週三發布）。新增資料源前先查證實際發布週期，避免對使用者做出不實際
的即時性承諾。

### [PAT-11] 技術可存取 ≠ 有權限抓取
**規則**：HTTP 200 不代表可以爬，須另查 robots.txt／平台條款（Threads
robots.txt 明確禁止自動化收集且點名擋 ClaudeBot）。技術可行性與使用
授權是兩道獨立的檢查，缺一不可。

### [PAT-12] 多來源首次執行閘門判斷用 any()，不是 all()
**規則**：混合「已有歷史的舊來源」與「全新來源」時，`all(count==0)`
會被舊來源拖累、誤判非首次而讓閘門失效；須用 `any(count==0)`。

### [PAT-13] 模擬持倉(紙上帳戶)設計決策紀錄（使用者確認2026-07-30）
**背景**：`schema.sql` 的 portfolio/position/trade_log 三張表、
`main.py` 的 `PORTFOLIO_CHANNELS`/`run_portfolio_channel()`、
`price_feed.py`、`ai_insight.build_trade_decision()` 都是這次對話確認
後新增的功能，設計決策只存在對話紀錄裡，特此記錄避免以後重新討論一次：

1. **3個獨立紙上帳戶**：`tw_stock`(1000 TWD)、`crypto_futures`
   (100 USDT，可做多做空、可用槓桿)、`crypto_discretionary`
   (100 USDT，只能做多、槓桿固定1)。純模擬追蹤，不動用真實資金。
2. **決策來源**：AI(Gemini)讀取對應大總結頻道(`tw_stock_meta`/
   `crypto_meta`)已產出的報告內容，自主判斷進出場，不需人工核准/介入。
3. **下注比例**：AI自己決定`cash_ratio`(動用多少比例的現金)，不設固定
   上限，目標是小額本金極大化報酬(複利滾大)，`main.py`只做防呆
   clamp(0~1)，不做業務邏輯上的比例限制。
4. **標的範圍**：不限制白名單，大總結報告提到什麼標的就可以交易什麼。
5. **執行時機**：`tw_stock`帳戶在台股收盤(13:30)後執行(14:00 cron)，
   讀取「最近一次」(不限定當天)的`tw_stock_meta`報告——見
   `db.get_latest_summary()`，因為當天的新報告要等20:30晚間彙整批次
   才會產出，收盤時點只有前一晚的報告可用。`crypto_futures`/
   `crypto_discretionary`帳戶每小時執行一次(見`scripts/setup_scheduled_tasks.ps1`——
   本專案實際部署在Windows Task Scheduler上,不是cron,`crontab.example`已移除)，
   即使大總結報告當天未更新，現價變化仍可能觸發平倉/加碼。
6. **即時價格來源(已實作,2026-07-30)**：`price_feed.get_price()`依symbol
   格式路由——純數字視為台股代號,查`openapi.twse.com.tw/v1/exchangeReport/
   STOCK_DAY_ALL`(官方開放資料,免key,無robots限制,已直接curl驗證,一個
   process內只查一次全市場快照後記憶體快取)；其餘視為加密貨幣代號,查
   Binance公開行情`api.binance.com/api/v3/ticker/price?symbol={SYM}USDT`
   (免key,官方文件本來就是給程式化查價用的)。**刻意不採用**
   `mis.twse.com.tw`的盤中即時報價端點——那是TWSE未正式開放的內部端點,
   `robots.txt`明確`Disallow: /`,且即時報價在台灣是TWSE的商業產品,依
   PAT-11「技術可存取≠有權限抓取」判斷不採用；台股帳戶反正只在收盤後
   跑一次，STOCK_DAY_ALL的收盤價已經是需要的「當下市價」，不需要真正
   盤中即時報價。查不到/查詢失敗一律回傳None(不拋例外)，呼叫端沿用
   「None就跳過這筆」的既有邏輯，不會用假資料頂替。
7. **PnL公式(已用db.open_position()/close_position()實測驗證)**：
   `margin_used = avg_cost*quantity/leverage`(開倉扣除)；多單
   `pnl=(price-avg_cost)*quantity`，空單`pnl=(avg_cost-price)*quantity`；
   平倉歸還`current_cash += margin_used + pnl`。leverage=1時
   margin_used等於全額本金，跟現貨/台股語意一致。

**現狀(2026-07-30更新)**：`price_feed.py`已實作真實API(見PAT-14下方)，
5個排程工作已在Windows Task Scheduler建立並驗證成功，功能已完整可運作。

### [PAT-14] 本專案實際部署在Windows Task Scheduler，不是cron；crontab.example已移除
**背景**：`crontab.example`從專案初期就存在，內容是Linux crontab語法、
路徑寫死`/opt/intel-pusher`，但專案從來沒有真的部署到Linux主機——一直
是跑在使用者這台Windows機器的工作排程器上(既有`IntelPusher-FedDaily`/
`IntelPusher-Weekly`/`IntelPusher-Backup`三個工作為證)。2026-07-30這次
對話裡，AI依`crontab.example`的路徑慣例假設有Linux伺服器、建議SSH部署，
使用者實際貼到PowerShell執行才發現完全兜不起來，浪費了一輪來回。

**教訓**：不能只憑文件內容(尤其是`*.example`這種可能從沒被驗證過的檔案)
判斷部署環境，要先用`Get-ScheduledTask`/實際環境查證。已移除
`crontab.example`，改用`scripts/setup_scheduled_tasks.ps1`(用
`Register-ScheduledTask`比照既有工作的命名/action模式)作為唯一的排程
部署方式，且是可執行腳本而非純文件，不會再脫離現實。

**踩過的坑**：`New-ScheduledTaskTrigger`的`-RepetitionDuration`不接受
`[TimeSpan]::MaxValue`(會產生`P99999999DT23H59M59S`，Task Scheduler判定
超出範圍而整個Register-ScheduledTask失敗)，要表示「近乎無限期重複」須用
一個夠大但合法的值，例如`(New-TimeSpan -Days 3650)`(10年)。此外
`Register-ScheduledTask`預設ErrorAction為Continue，呼叫失敗不會中斷腳本，
若沒有額外`-ErrorAction Stop`+try/catch，後面的`Write-Output "已建立"`
還是會執行、產生錯誤的成功訊息——任何類似的「建立/註冊」函式都要包
try/catch才能讓成功訊息可信。

### [PAT-15] 模擬持倉決策輸入強化：歷史交易紀錄+多天報告趨勢+技術指標(2026-07-30)
**背景**：`build_trade_decision()`原本每次只餵給AI「今天(或最近一次)的
單一報告文字+目前持倉現價」，AI等於每次都是失憶重新判斷，沒有「上次
類似情況做過什麼、結果如何」的記憶，也沒有數字化的趨勢資訊。使用者要求
「快速調閱、低負擔」，故只加低成本的資訊來源，不是重新設計架構：

1. `db.get_recent_trades(portfolio_id, limit=10)`——該帳戶近期trade_log，
   純DB查詢零成本。`build_trade_decision()`的`recent_reports`參數現在是
   **必填**且呼叫端須保證非空(main.py用它取代原本的單一`report_text`
   做為「有無報告」的判斷閘門，改用`db.get_recent_summaries()`回傳list)。
2. `db.get_recent_summaries(source_id, limit=5)`——同一來源近5筆已存檔
   報告(依fetched_at新到舊)，讓AI能比對「這幾天報告怎麼變化」，同樣是
   純DB查詢。
3. `price_feed.get_technical_snapshot(symbol)`——SMA5/SMA20/5日與20日
   漲跌%，純程式計算(不耗AI額度)。台股用`www.twse.com.tw/exchangeReport/
   STOCK_DAY`(個股歷史,已直接curl驗證,只抓近2個月非etf0050.py的7個月，
   因為只需要SMA20不需要MA120)；幣圈用Binance klines
   (`interval=1d&limit=20`)。**只enrich目前持倉**(main.py呼叫端邏輯)，
   不是每個AI可能想交易的新標的都算——AI選新標的的依據仍是報告敘事，
   技術指標查詢失敗(`get_technical_snapshot()`回傳None)不影響本次執行
   (跟`current_price`不同，那個缺了要整批放棄，這裡只是輔助資訊)。

**已用真實Gemini呼叫驗證**(mock持倉+歷史資料,未寫入db/未觸發webhook)：
AI的reasoning正確引用了SMA5/SMA20數字做判斷依據，證實enrichment確實
被讀取並納入推理，不只是塞進prompt沒被使用。

### [PAT-16] Gemini API Key「建立專案」≠「可用額度」，四層要分開查證
**背景**：從已棄用的scholarship-monitor舊專案(2026-07-05,已刪除,見PAT-17)
搬移過來的教訓——當時使用者回報「已建立Google Cloud專案」，重新驗證卻發現
`.env`裡的key前綴/長度跟先前完全相同，`generateContent`仍回報HTTP 429，
quotaId明確帶`-FreeTier`且`limit:0`，代表額度從一開始就是0，不是「用完了」。

**教訓**：以下四件事是各自獨立、要分開查證的層次，任一層沒過都會導致
`generateContent`失敗，不能假設「上一層通過=下一層也通過」：
1. Google Cloud專案是否建立
2. API key是否真的綁定到**該**專案(比對key前綴/長度是否真的變了，是
   快速判斷使用者是否真的換了新key的低成本方法)
3. 該專案是否已連結有效的計費帳戶(建立專案不會自動連結)
4. 對應模型的免費額度是否真的非0(某些帳戶類型/地區的免費方案對特定
   模型額度就是0，不是「額度用盡」，需要連結計費才會核發非零額度)

**如何查**：`ListModels`呼叫成功只代表身分驗證有效(key沒壞)，不代表
`generateContent`會成功；quotaId字串裡帶`-FreeTier`+`limit:0`是明確判斷
「從一開始就沒有額度」而非「暫時用完」的關鍵線索。本專案`ai_insight.py`
所有函式已經統一用「api_key不存在就直接回傳None」+外層log錯誤的模式，
若未來真的遇到「key存在但呼叫持續失敗」，先照這四層排查，不要當成
單純的網路/重試問題。

### [PAT-17] legacy/scholarship-monitor/ 已於2026-07-30移除
**背景**：scm專案(scholarship-monitor)已於2026-07-28棄用，內容合併進本
專案，`legacy/scholarship-monitor/`資料夾是那次合併留下的完整封存
(README/CLAUDE.md/Meta_Dev_Knowledge.md等)。複查後確認裡面8段標記
「Universal Principle」的內容中，兩段真正跟本專案相關且已搬移(見PAT-16、
本專案`db.py`的source_id/dedup_key一致性設計)，其餘因前提不成立
(該專案有「禁止AI」的硬性限制，本專案已大量使用Gemini，前提不同)或
過於專案特定(該專案的exclude_terms.json分類陣列結構)而未搬移。已用git
`rm`整批移除，舊內容仍可透過git history查閱(commit移除前的版本)。

**跨模組識別碼比對的教訓(從legacy MOE健康分數靜默失效bug抽取,通用原則)**：
同一個資料實體(如來源網址/ID)在不同模組間流動時，若中間某環節把它換成
語意相近但字面不同的值，後續任何用「完全比對」銜接的地方都會靜默斷裂——
不拋錯、不易被發現，只有「監控/比對」這一層悄悄失靈，其餘功能表面正常。
本專案`source_id`(如`digest_report.tw_stock_meta`)/`dedup_key`這類跨
模組比對機制，新增來源或修改source_id命名時要留意這個風險：確保
production/schema/呼叫端三處對同一個ID的拼法完全一致，不要出現「A模組
存的是這個字串、B模組查的是語意相同但拼法不同的字串」的情況。

### [PAT-18] 台灣實習頻道：關鍵字過濾+Gemini最終消歧的兩層架構(2026-07-30)
**背景**：使用者要求新增「台灣實習」頻道，比照獎學金頻道模式(不套用
scholarship_util的學校/年級/身份別排除規則——科系與資歷不設限，使用者
確認)。資料源用勞動部台灣就業通開放資料(`apiservice.mol.gov.tw/OdService/
rest/datastore/A17000000J-030144-VAL`，見`scrapers/internship_mol.py`)，
免key、無robots限制，但需要`truststore.inject_into_ssl()`(同PAT-01的
TWCA憑證鏈問題)，且實測不穩定(3次測試2次逾時/斷線)，已加指數退避重試。
API不支援伺服器端關鍵字/offset篩選，固定回傳最新1000筆全國職缺快照。

**核心發現：「實習」在中文職缺文本裡是多義詞**，除了「學生實習」，還常見：
(a) 新人試用期/教育訓練話術(「安排實習及數位課程訓練」)、(b) 應徵資格
要求(「具...實習經驗」是要求應徵者已有實習經歷，不是提供實習)、
(c) 實習工場/實習教室等設施名稱、(d) 職務描述提到「規劃實習專案」(要
應徵者管理別人的實習，不是本身是實習)。純關鍵字regex排除規則測不出
這些語境差異(2026-07-30實測：純用「實習」二字precision約27%；改用
複合詞如「實習生」precision 100%但漏收「工讀生｜實習」這種用詞分散的
真實職缺；混用+noise_regex排除也只到約60%)。

**解法**：兩層架構，比照本專案「結構化用程式、非結構化才用AI」的既有
原則(`Meta_Dev_Knowledge.md`低成本精神)——
1. `internship_util.is_relevant()`：純規則關鍵字計分，從1000筆原始職缺
   篩到約10筆候選(99%篩除率，零AI成本)。
2. `ai_insight.classify_internships()`：只對這約10筆候選呼叫一次Gemini
   做語意消歧，不是對全部1000筆呼叫，符合低成本原則。已用真實資料驗證：
   AI正確排除4筆假陽性(HR職務描述/新人訓練話術/資格要求/設施名稱)，
   保留6筆真實職缺(含3筆容易被規則排除的「校外實習/建教合作」附加說明
   職缺)，跟人工判斷結果完全一致。

**強制規則**：AI呼叫失敗(`classify_internships()`回傳None)時退回沿用
關鍵字過濾結果，不整批放棄推播——AI是精準度加強層，不是必要閘門，這層
失敗不該讓整個頻道停擺(比照本專案其餘AI功能「失敗就退回較保守的既有
邏輯」的一貫模式)。

### [PAT-19] 6個晚間彙整頻道從未被排程過(2026-07-30發現)
**背景**：使用者質疑「日報是不是沒有跑通」，查`work/activity.log`發現
`tsmc_digest`/`cbc_digest`/`us_stock_digest`/`crypto_digest`/
`macro_tech_digest`/`geopolitics_digest`這6個從專案初期就存在的頻道，
最後一次執行是2026-07-29深夜到2026-07-30凌晨(開發測試時手動跑的)，
`Get-ScheduledTask`確認Windows Task Scheduler裡完全沒有註冊這6個。
`macro_fred`/`twse_tsmc`/`twse_chunghwa`這3個原本`crontab.example`裡
daily 9am的項目也一樣缺排程。

**根因**：PAT-14移除`crontab.example`(那份文件對應的是從未真的部署過的
Linux主機)、改建`scripts/setup_scheduled_tasks.ps1`時，只針對「這個
session正在新增的功能」(模擬持倉/大總結/實習頻道)建了排程，沒有回頭
把`crontab.example`原本涵蓋的舊功能也一併遷移過去——導致這9個排程
「表面上被crontab.example文件記錄過」，但實際上從來沒有在任何真正的
排程機制(不管是虛構的Linux cron還是真實的Windows Task Scheduler)裡
自動執行過。**教訓**：移除一份舊排程文件時，要逐條核對每一項功能都有
對應的新排程，不能只顧著新增的部分。

**修復**：`scripts/setup_scheduled_tasks.ps1`補上這9個工作(6個晚間彙整
20:00 + macro_fred平日9:00 + twse_tsmc/twse_chunghwa每日9:00)，已執行
並用`Get-ScheduledTask`確認全部18個IntelPusher-*工作都在。

**同類疑似缺口(未修復，待使用者決定)**：`main.py`有`--scholarship`
批次模式(`run_scholarship()`)，但`IntelPusher-Scholarship`同樣不存在於
排程清單——不確定使用者是否刻意手動觸發，未擅自新增排程。

### [PAT-20] 模擬持倉「觀望不動作」的理由曾被吞掉
**背景**：使用者發現Discord訊息只顯示「0個動作」，看不到AI為什麼決定
不動作。`run_portfolio_channel()`原本的hold分支只在`a["symbol"]`非空時
才把理由加進`action_lines`，但AI對「整體觀望、沒有特定標的」的hold
決策通常不會填symbol，導致理由寫進了`db.trade_log`卻不會出現在Discord
訊息裡——使用者看到的畫面等於「什麼都沒發生」，但AI其實有做判斷、
只是沒被看見。

**修復**：不論`a["symbol"]`是否為空都要把理由加進`action_lines`，只是
文案不同(有標的用「持有 {symbol}」，沒有就用「觀望」)。**強制規則**：
任何AI決策結果(不只hold)都必須在推播訊息裡呈現理由，不能因為「這個
分支剛好沒有一個明顯的顯示欄位」就讓理由消失——使用者需要知道「為什麼
不動作」跟「為什麼動作」一樣重要。

### [PAT-21] run_scholarship()/run_internship()漏掉mark_published，重蹈PAT-03的坑
**背景**：2026-07-30測試Telegram路由時查db發現，今天推播成功的135筆
獎學金/實習項目，`item.status`全部卡在`'new'`，不是`'published'`——
兩個函式的成功推播分支都沒有呼叫`db.mark_published()`(對照
`run_digest_channel()`/`run_meta_summary_channel()`/`run_source()`都有
呼叫)。這正是PAT-03已經記錄過、警告過的「status欄位雙重語意風險」，
但沒有被套用到後來新增的這兩個pipeline——**教訓**：專案自己的knowledge
base裡的規則，新增類似功能時要主動核對是否適用，不能只在事後被問題
逼出來才想起。

**影響評估**：不影響去重(dedup_key才是去重依據，不是status)，純粹是
status語意不誠實；但若未來有功能誤用`status='new'`當作「待處理」的
篩選條件，會把這些已經真正推播過的項目誤判成還沒處理過，重蹈PAT-03
描述的同一種錯誤。

**修復**：兩個函式都補上「all_ok旗標+推播全部成功才mark_published」的
模式(跟run_digest_channel()一致)。並對`data.db`做一次性回填：135筆
今天已確認推播成功(Discord log/Telegram補發通知都證實過)的項目，從
`'new'`改成`'published'`(限定`source_id LIKE 'scholarship%' OR
'internship%'` + `fetched_at`是今天，避免誤改到其他來源或其他日期的
資料)。
