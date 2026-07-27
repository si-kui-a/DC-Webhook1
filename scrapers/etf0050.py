"""
scrapers/etf0050.py — 元大台灣50（0050）持股比重。

已驗證來源（2026-07-27 curl 實測確認）：
- /product/detail/0050/ratio 頁面為 Nuxt.js SSR 渲染，資料不在 <table> 裡，
  而是以 <div class="td" data-v-818b5120> 結構輸出，每個欄位依序為：
  商品代碼 / 商品名稱 / 商品數量 / 商品權重，4 個 div 為一組。
  用 BeautifulSoup 解析 div.td 即可取得完整持股清單，不需要 headless browser。
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


def _is_stock_weight_label(label: str) -> bool:
    """篩選出屬於股票權重表的欄位標籤，排除期貨等其他區段。"""
    return label in ("商品代碼", "商品名稱", "商品數量", "商品權重")


def fetch(top_n: int = 10) -> list[dict]:
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    # Nuxt SSR 結構：div.each_table > div.table > div.tbody > div.tr > div.td
    # 每個 div.tr 包含 4 個 td：代碼/名稱/數量/權重
    for tr in soup.select("div.tbody div.tr[data-v-818b5120]"):
        cells = tr.select("div.td[data-v-818b5120]")
        if len(cells) < 4:
            continue
        labels = [c.select_one("span:first-child").get_text(strip=True) for c in cells[:4]]
        if not all(_is_stock_weight_label(l) for l in labels):
            continue  # 不是股票權重表（例如期貨區段）跳過
        values = [c.find_all("span")[-1].get_text(strip=True) for c in cells[:4]]
        # 檢查權重欄位是否為合理百分比（0~100），且股票代碼為 4 位數
        try:
            w = float(values[3].replace("%", ""))
            if w < 0 or w > 100:
                continue
            if not values[0].isdigit() or len(values[0]) != 4:
                continue  # 排除期貨（TX/NYF 等非 4 位數字代碼）
        except ValueError:
            continue
        rows.append((values[0], values[1], values[2], values[3]))

    if not rows:
        raise RuntimeError(
            "抓取 0050 持股比重失敗：頁面結構可能已變動，"
            "div.tbody div.tr 未找到股票權重資料"
        )

    top_holdings = rows[:top_n]
    today = date.today().isoformat()
    summary = "\n".join(
        f"{r[1]}（{r[0]}）：{r[3]}%"
        for r in top_holdings
    )

    return [{
        "title": f"0050 持股比重快照（{today}）",
        "summary": summary,
        "url": BASE_URL,
        "published_at": today,
    }]
