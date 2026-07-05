"""
scrapers/etf0050.py — 元大台灣50（0050）持股比重。

已驗證來源（直接 curl 確認，非搜尋推測）：
- /product/detail/0050/newsAnnounce 與 /product/detail/0050/ratio 兩個路徑
  curl 實測都只回傳「幾乎空殼」的 HTML（無 __NEXT_DATA__、無 <table>），
  內容由前端 JS 在瀏覽器端動態載入，requests + BeautifulSoup 看不到。
- 修復需要 headless browser（如 Playwright），屬架構升級，需核准後才動手，
  見 README 已知限制，不自行加裝。

原版在抓不到 <table> 時會回傳一筆 summary 為「（抓取失敗或頁面結構已變動，
需人工核對）」的「假快照」item——main.py 會把它當成正常抓取結果寫入 db 並
推播到 Discord，等於每次執行都對外送出一則沒有實際資訊的訊息。已改成在
抓不到資料時直接 raise NotImplementedError，讓 main.py 走既有的例外處理
路徑（計入 fail_count、連續失敗會發 critical log），跟其他尚未實作來源
行為一致，不會偽裝成「有推播內容」。
"""
from datetime import date

import requests
from bs4 import BeautifulSoup

SOURCE_ID = "etf0050.holdings"
SOURCE_NAME = "元大台灣50-持股比重"
BASE_URL = "https://www.yuantaetfs.com/product/detail/0050/ratio"

HEADERS = {
    "User-Agent": "IntelPusher/0.1 (personal research bot; contact: your-email@example.com)"
}


def fetch(top_n: int = 10) -> list[dict]:
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for tr in soup.select("table tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 4:
            rows.append(cells)

    if not rows:
        raise NotImplementedError(
            "etf0050 頁面為前端 JS 動態渲染（curl 已確認無 <table>），"
            "requests+BeautifulSoup 抓不到資料。需要 Playwright 才能解決，"
            "屬架構升級，待核准。見 README 已知限制。"
        )

    top_holdings = rows[:top_n]
    today = date.today().isoformat()
    summary = "\n".join(f"{r[1]}：{r[3]}%" for r in top_holdings if len(r) >= 4)

    return [{
        "title": f"0050 持股比重快照（{today}）",
        "summary": summary,
        "url": BASE_URL,
        "published_at": today,
    }]
