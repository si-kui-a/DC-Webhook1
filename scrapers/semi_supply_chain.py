"""
scrapers/semi_supply_chain.py — 半導體與 AI 供應鏈（#semi-ai-supply-chain）。

狀態：STUB。需要你決定範圍後才能完成實作。

== 2026-07 來源調查結果 ==

### MOPS（公開資訊觀測站）
- 重大訊息查詢可用 POST → https://mops.twse.com.tw/mops/web/t05st01_ifr
- 參數：step=1, TYPEK=sii, year=115, month=7, co_id=2330
- **問題**：實測回傳 406「查無相符資料」，且需要 ASP.NET ViewState/
  Session 管理才能建立合法查詢。跟 moe.py 的 WebForms 處理類似，但 MOPS
  還有 CSRF/session 阻擋，非單純 postback。
- 如果要繞過，需實作：取得 session cookie → 下載 iframe 頁面 → 解析
  __VIEWSTATE/__EVENTVALIDATION → 帶 state 提交查詢 → 解析 JSON 回傳。
- 實作成本中高，且 MOPS 有時會變更安全機制。

### TWSE Open API（已驗證可用）
- https://openapi.twse.com.tw/v1/opendata/t187ap03_L（列出所有上市公司基本資料）
- 不需 session / ViewState，直接回傳 JSON（1092 筆，encoding Big5→UTF-8）
- 但這只有公司基本資料，沒有「重大訊息」或「最新公告」。
- 沒有重大訊息的公開 JSON API。

### 建議方案
如果你想要「半導體供應鏈公司的最新公告」功能，最可行的路線是：
1. 你提供半導體公司股票代碼白名單（例如 2330 台積電、3711 日月光投控、
   2449 京元電、3131 弘塑、6187 萬潤、2454 聯發科等）
2. 實作方式有兩種選擇：
   A. **(推薦) TWSE 基本資料 + 各公司 IR 頁面**：比照 tsmc.py 模式，
      對白名單中每家公司抓其官網投資人專區，只需 requests+BeautifulSoup。
      優點：穩定、無 session 管理問題。缺點：每家公司頁面結構不同。
   B. **MOPS 重大訊息**：需實作 ASP.NET ViewState 處理（同 moe.py）。
      優點：統一介面。缺點：需處理 session 管理，且 MOPS 偶有維護。

### 現狀
本檔案維持 stub，已將 curl 實測結果與技術路線寫在上面。
待你決定白名單與路線後，再補完實作。
"""


def fetch() -> list[dict]:
    """
    尚未實作。實作前需要你決定兩件事：
      1. 半導體公司股票代碼白名單（如 2330,3711,2449,...）
      2. 路線 A（各公司 IR 頁面）或路線 B（MOPS WebForms）
    決定後可補上實作。
    """
    raise NotImplementedError(
        "半導體供應鏈來源尚未實作。"
        "請先提供公司代碼白名單，並選擇路線 A（IR 頁面）或路線 B（MOPS），見本檔案 docstring。"
    )
