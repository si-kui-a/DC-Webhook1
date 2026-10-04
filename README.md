# Intel Pusher — 股票/獎學金/實習/行事曆情報推播機器人

不用 n8n。純 Python + SQLite + Windows Task Scheduler。理由見企劃報告結論：n8n 的核心優勢（免寫 code 的視覺化維護）在「用 Claude 直接寫程式碼」的情境下不成立，只會多一層部署成本。

## 安裝

```bash
cd intel-pusher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env，依 .env.example 填入所需的 Discord Webhook URL(數量會隨頻道增減，不寫死)
```

## 執行

```bash
python main.py --source fed       # 單一來源測試
python main.py --source all       # 跑SOURCE_REGISTRY內建的所有即時推播來源(不含彙整/批次類，見下方"目前啟用的來源/頻道")
```

第一次執行會自動建立 `data.db`（SQLite），不需要額外安裝資料庫。

## 目前啟用的來源/頻道

這份清單以前是手寫表格，寫於專案第一天(還在講「沙盒環境連不上網站」
「selector 待實測」)，一個多月來所有來源早就上線，但表格從沒人更新過，
變成主動誤導(例如曾長期寫著「半導體供應鏈未實作」，其實已經是正常運作
中的頻道)。改成執行下面這行直接看：

```bash
python scripts/show_source_status.py
```

輸出直接讀`jobs/*.py`裡的registry dict，永遠反映當下實際狀態，不需要
手動維護——刻意不在這裡寫死總數，寫了就是重蹈被取代那份表格的覆轍。
排程頻率/是否真的有排上Windows Task Scheduler不在這支腳本範圍內，查
`Get-ScheduledTask -TaskName "IntelPusher-*"`。

## 已知限制

- **JS 動態渲染頁面**：`requests` 只拿到伺服器回傳的原始 HTML，不會執行 JavaScript。如果某來源的資料是靠前端 JS 抓 API 填進去的，`requests+BeautifulSoup` 會抓空，需改用 Playwright 渲染後再解析。
- **排程靠 GitHub Actions**：`scheduler.yml` 循序執行 `scripts/cloud_scheduler.py`，見 `docs/operations/CLOUD_SCHEDULER.md`。本機排程與 `backup.sh` 已於 2026-10-04 移除。
- **健康監控**：抓取失敗記在本地 log 與 SQLite 的 `fail_count`(見`scrapers/health.py`)，連續失敗達門檻時個別來源會在 log 標記，沒有另外接外部告警管道。

## 租屋搜尋（零 AI）
租屋來源不自動猜測；以 `RENTAL_FEED_URLS` 明確列出已獲授權的 RSS、JSON 或 HTML 來源。可用 `RENTAL_AREAS`、`RENTAL_MAX_MONTHLY`、`RENTAL_MIN_PING`、`RENTAL_KEYWORDS`、`RENTAL_EXCLUDE` 篩選。
設定 `WEBHOOK_RENTAL_SEARCH` 後執行 `python main.py --source rental_search`；未設定來源時會安全地記錄為未配置，不會抓取未知網站。

來源目錄另收錄臺北市、新北市政府開放資料、國家住宅及都市更新中心與新北住都中心公告；各來源先經 PENDING_REVIEW，確認實際下載端點與欄位後才啟用。

租屋目標設定：`config/RENTAL_SEARCH_PROFILE.yaml` 固定以東海大學為錨點、30 分鐘大眾運輸為上限；沒有來源通勤時間時只做站點初篩並標示需複核。

目前租屋偏好：月租 3,000–4,500 元、4,000 元以下優先、套房/獨立套房優先；女性學生條件需房源明確標示或人工確認。

租金目標以租屋補助後實付計算：3,000–4,500 元（4,000 元以下優先）；來源未提供補助金額時標記補助待核。

已追加東海學生專門來源：雲端租屋生活網、生活輔導組校外賃居服務、柯比意東海租屋；前兩者待端點驗證，柯比意待授權。

開伙條件採偏好排序：可開伙加分；不可開伙與未標示仍納入並標記。
