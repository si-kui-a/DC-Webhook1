# Intel Pusher — 最快可行方案（MVP 骨架）

不用 n8n。純 Python + cron + SQLite。理由見企劃報告結論：n8n 的核心優勢（免寫 code 的視覺化維護）在「用 Claude 直接寫程式碼」的情境下不成立，只會多一層部署成本。

## 安裝

```bash
cd intel-pusher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env，填入 4 組 Discord Webhook URL
```

## 執行

```bash
python main.py --source fed       # 單一來源測試
python main.py --source all       # 全部 4 個核心來源
```

第一次執行會自動建立 `data.db`（SQLite），不需要額外安裝資料庫。

## 已完成 vs 尚未完成

| 來源 | 狀態 | 說明 |
|---|---|---|
| Fed（`fed.py`） | 可執行，selector 待實測 | 已確認 URL：`federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm`。.gov 官方頁，非 JS 動態渲染，穩定性最高，**建議第一個測試**。 |
| 台灣央行（`cbc.py`） | 可執行，selector 待實測 | 已確認 URL：`cbc.gov.tw/tw/lp-302-1.html`。政府 CMS 系統，URL pattern 沿用多年。 |
| 台積電（`tsmc.py`） | 可執行，selector 待實測，**有風險** | IR 頁面疑似部分內容 JS 動態渲染，若 `requests+BeautifulSoup` 抓不到資料，需改用 Playwright 渲染後再解析（見下方「已知限制」）。 |
| 0050（`etf0050.py`） | 可執行，selector 待實測 | 已確認 URL：`yuantaetfs.com/product/detail/0050/ratio`，頁面有清楚的持股表格。 |
| 半導體供應鏈（`semi_supply_chain.py`） | **未實作（stub）** | 未找到穩定 A 級來源。DIGITIMES 需訂閱、反爬防護強；MOPS 可行但需要你先確認「前中段供應鏈」公司代碼白名單。**這是唯一卡住的項目，其餘 4 個可以先跑。** |

## 已實測狀態（誠實揭露）

我在自己的沙盒環境跑了 `python main.py --source fed/cbc/etf0050` 做真實抓取測試，結果是：**全部被我方沙盒的網路白名單擋下（HTTP 403, `x-deny-reason: host_not_allowed`），不是目標網站拒絕**。我的執行環境只能連線到 pypi、github、npm 等開發用網域，無法連到 `federalreserve.gov`、`cbc.gov.tw`、`yuantaetfs.com` 這類一般網站。

這代表：

- **程式碼語法與模組邏輯已驗證無誤**（DB 初始化、Webhook payload 建構、模組匯入全部通過）。
- **實際 HTML 結構是否符合 selector 假設，我這邊沒有辦法驗證**——因為我的環境連不上這些網站，只能靠先前搜尋結果的文字內容合理推測 selector 寫法。
- **你在自己的機器上執行時，這些請求會是正常的**（你的機器沒有我這種網域白名單限制），但這也代表**第一次真實執行的結果現在無法預先保證**。

**你拿到這份骨架後，請先在你自己的機器跑一次 `python main.py --source fed`，把 log 貼給我，我可以直接看真實 HTML 結構把 selector 修到能動為止**——這比我在無法連線的環境裡繼續猜測 selector 更有效率。

## 為什麼每個 scraper 都寫「selector 待實測」

我用搜尋工具確認了每個來源的**網址**存在且內容相關，但沒有實際渲染頁面去讀 HTML 結構、也沒有執行程式碼驗證 selector 抓不抓得到資料。這代表：

1. `requests.get()` 抓到的原始 HTML，跟你在瀏覽器看到的畫面，如果該頁面用 JS 動態載入內容，兩者會不一樣。
2. 目前 scraper 裡的 CSS selector（例如 `soup.select("a[href*='investor-meetings']")`）是根據頁面**性質**合理推測寫的，不是根據抓到的真實 HTML 反推的。

**這不是「還沒做完」，是誠實標註「這裡有一步驗證還沒做」**——比起寫一個看起來完整但實際跑不出結果的 scraper，先讓你知道哪一步需要你或我再花 10 分鐘打開瀏覽器 F12 核對一次，風險更低。

## 下一步（每個 scraper 各約 10–15 分鐘）

1. 執行 `python main.py --source fed`。
2. 若 log 顯示「本次無新資料」但你確定該頁面有內容，代表 selector 沒抓到東西——需要用瀏覽器開發者工具（F12）打開該網址，找到實際的 HTML 結構，回來調整 `scrapers/fed.py` 裡的 `soup.find_all(...)` 條件。
3. 依序對 `cbc.py`、`etf0050.py`、`tsmc.py` 做同樣的事。
4. 半導體供應鏈：先決定 MOPS 白名單或 DIGITIMES 標題頁，我再補完整實作。

## 已知限制

- **JS 動態渲染頁面**：`requests` 只拿到伺服器回傳的原始 HTML，不會執行 JavaScript。如果某來源的資料是靠前端 JS 抓 API 填進去的，`requests+BeautifulSoup` 會抓空。解法：改用 `playwright`（`pip install playwright && playwright install chromium`），用無頭瀏覽器渲染後再解析，但這會讓每次抓取變慢（多 2–5 秒）且多一個系統依賴。
- **單機單點故障**：目前沒有背景常駐 process，靠 cron 觸發，若主機關機則不會補跑錯過的排程（cron 本身不做這件事）。若在意這點，之後可加 `anacron` 或改用 systemd timer。
- **無外部健康監控**：目前失敗只寫進本地 log 與 SQLite 的 `fail_count`，沒有主動通知你。等前 4 個來源跑穩後，可以加一支獨立的「健康檢查 Webhook」，在連續失敗達 `FAIL_THRESHOLD`（目前設 3 次）時推播告警。

## GitHub 自動備份

不使用 GitHub Actions（那是在 GitHub 雲端執行，違反本專案「全部依靠本地端運行」的原則）。改用本地 cron 觸發本地 `git push`，GitHub 只是異地儲存目的地。

### 設定步驟（僅需一次）

```bash
cd intel-pusher
git init
git remote add origin git@github.com:你的帳號/你的私有倉庫.git   # 建議用 SSH deploy key，僅授予 push 權限
```

### 含個資檔案的處理

`encrypt_backup.py` 的 `PII_FILES` 清單目前是空的——intel-pusher 本身不產生個資檔案（沒有 `academic_progress.md`、`my_resume.json` 這類東西，那是另一個專案的殘留設定）。這支腳本保留是為了未來如果這個專案真的產生需要加密備份的個資檔案時，直接把檔名加進 `PII_FILES` 就能用，不需要重寫備份流程。

真正需要保密、不進版控的是 `.env`（Webhook URL）與 `data.db`（去重資料庫），這兩者單純靠 `.gitignore` 排除，不加密、不備份到 GitHub。

若之後這個清單真的不再是空的，金鑰會存在 `~/.intel-pusher-backup.key`（權限 600，只有你能讀），**這把金鑰本身不會被備份**——如果你要保留解密能力，請自行把這把金鑰異地備份一份（例如密碼管理器），遺失金鑰代表無法還原歷史加密備份。

### 執行備份

```bash
chmod +x backup.sh
./backup.sh   # 手動測試一次
```

確認無誤後，依 `scripts/setup_scheduled_tasks.ps1` 建立 Windows 工作排程即可全自動運作
（本專案實際跑在 Windows Task Scheduler 上，不是 cron——`crontab.example` 是早期
規劃階段假設的部署方式，跟實際情況不符，已移除）。

## 目錄結構

```
intel-pusher/
├── main.py              # 執行入口
├── db.py                # SQLite 存取層
├── push_webhook.py       # 通用 Discord 發送器（29 個頻道共用）
├── schema.sql            # 資料表定義
├── scrapers/
│   ├── tsmc.py
│   ├── fed.py
│   ├── cbc.py
│   ├── etf0050.py
│   └── semi_supply_chain.py   # stub，未實作
├── .env.example
├── requirements.txt
└── scripts/
    └── setup_scheduled_tasks.ps1   # 建立Windows Task Scheduler排程(需系統管理員)
```

## ���ηj�M�]�s AI�^
���Ψӷ����۰ʲq���F�H `RENTAL_FEED_URLS` ���T�C�X�w����v�� RSS�BJSON �� HTML �ӷ��C�i�� `RENTAL_AREAS`�B`RENTAL_MAX_MONTHLY`�B`RENTAL_MIN_PING`�B`RENTAL_KEYWORDS`�B`RENTAL_EXCLUDE` �z��C
�]�w `WEBHOOK_RENTAL_SEARCH` ����� `python main.py --source rental_search`�F���]�w�ӷ��ɷ|�w���a�O�������t�m�A���|������������C

來源目錄另收錄臺北市、新北市政府開放資料、國家住宅及都市更新中心與新北住都中心公告；各來源先經 PENDING_REVIEW，確認實際下載端點與欄位後才啟用。

租屋目標設定：`config/RENTAL_SEARCH_PROFILE.yaml` 固定以東海大學為錨點、30 分鐘大眾運輸為上限；沒有來源通勤時間時只做站點初篩並標示需複核。

目前租屋偏好：月租 3,000–4,500 元、4,000 元以下優先、套房/獨立套房優先；女性學生條件需房源明確標示或人工確認。
