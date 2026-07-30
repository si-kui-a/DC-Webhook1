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
   `crypto_discretionary`帳戶每小時執行一次(見`crontab.example`)，
   即使大總結報告當天未更新，現價變化仍可能觸發平倉/加碼。
6. **即時價格來源尚未實作**：`price_feed.get_price()`目前是
   `NotImplementedError`的stub，使用者本人負責接上場外即時價格API
   (台股/加密貨幣)。在接上之前，所有`*_portfolio` cron job執行時都會
   在查價這步log錯誤並跳過，不會用假資料頂替、不會半套執行交易。
7. **PnL公式(已用db.open_position()/close_position()實測驗證)**：
   `margin_used = avg_cost*quantity/leverage`(開倉扣除)；多單
   `pnl=(price-avg_cost)*quantity`，空單`pnl=(avg_cost-price)*quantity`；
   平倉歸還`current_cash += margin_used + pnl`。leverage=1時
   margin_used等於全額本金，跟現貨/台股語意一致。

**待辦**：使用者接上`price_feed.py`的實際API後，這個功能才會真正開始
執行交易；在那之前`*_portfolio` cron job只會安靜跳過，屬預期行為。
