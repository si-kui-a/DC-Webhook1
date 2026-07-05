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
