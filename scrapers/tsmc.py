"""
scrapers/tsmc.py — 台積電新聞稿（月營收公告、法說會公告等重大訊息都會出現在這個清單中）。

已驗證來源（直接 curl 確認，非搜尋推測）：
- investor.tsmc.com/chinese、pr.tsmc.com/chinese/* 這些中文路徑目前會被
  Cloudflare bot 防護擋下（curl 實測回應 403 + Cf-Mitigated: challenge），
  單純 requests 拿不到內容，換 UA 也一樣是中文路徑本身被擋。
- pr.tsmc.com/english/latest-news 穩定可用（curl 實測 200、cf-cache-status: HIT），
  伺服器端渲染，結構為 <article class="... node--type-news ...">，
  內含 a[href]、time[datetime]、.article-title。範圍因此從「法說會活動」
  調整為「英文版新聞稿全清單」（含月營收報告、法說會公告等）。
- 用一般瀏覽器 UA（而非自報 bot 的 UA）以降低被 Cloudflare 判定為 bot 的機率，
  這點在 fed.py / cbc.py（政府網站，無此問題）不需要。
"""
import re

import requests
from scrapers import http_client
from bs4 import BeautifulSoup

# 已抽樣 6 篇 tsmc 新聞稿正文開頭（含 revenue report / shareholders meeting /
# board resolutions / M&A 公告 / 第三方聯名稿），確認固定的 dateline 樣板：
# "<城市>, Taiwan, R.O.C.[,.]? [–-] <月 日, 年> [–-] "。
# 已觀察到的變化：城市名大小寫不一（HSINCHU 全大寫 / Hsinchu 首字大寫，
# 但只出現過這一個城市，regex 仍用 [A-Za-z]+ 保留彈性以涵蓋其他城市）、
# R.O.C. 後面接的是句點+破折號還是逗號、破折號用 en dash（–）或連字號（-）
# 都有出現。這個 regex 涵蓋所有已觀察到的變化。
# 不修改 nltk 的 abbrev_types 來源，只在這裡（資料清洗階段）處理，
# 見 Meta_Dev_Knowledge.md PAT-06。
DATELINE_RE = re.compile(
    r"^[A-Za-z]+,\s*Taiwan,\s*R\.O\.C\.[,.]?\s*[–\-]?\s*[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*[–\-]\s*"
)

SOURCE_ID = "tsmc.press_releases"
SOURCE_NAME = "台積電-新聞稿"
BASE_URL = "https://pr.tsmc.com"
LIST_URL = f"{BASE_URL}/english/latest-news"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def fetch() -> list[dict]:
    resp = http_client.get(LIST_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for article in soup.select("article.node--type-news"):
        link_el = article.select_one("a[href]")
        title_el = article.select_one(".article-title")
        time_el = article.select_one("time[datetime]")
        if not link_el or not title_el:
            continue
        href = link_el.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        results.append({
            "title": title_el.get_text(strip=True),
            "summary": None,
            "url": url,
            "published_at": time_el.get_text(strip=True) if time_el else None,
        })
    return results


def fetch_detail_text(url: str) -> str:
    """抓單則新聞稿詳情頁的正文全文（供摘要用）。

    已驗證的結構（curl 實測 pr.tsmc.com/english/news/3320）：
    div.node__content 底下的 div.field--name-body 就是純正文欄位，
    跟同一頁的圖片欄位（field--name-field-image）、聯絡人資訊
    （.articleSubInfo）分開，不需要額外排除雜訊。
    """
    resp = http_client.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    body_div = soup.select_one("div.field--name-body")
    if not body_div:
        return ""
    text = body_div.get_text(separator=" ", strip=True)
    # 先把 dateline 樣板砍掉，摘要套件（sumy LexRank）才不會把 "R.O.C." 這種
    # 縮寫誤判成句尾，選出「HSINCHU, Taiwan, R.O.C....」這種近乎無用的
    # 短句當摘要。
    return DATELINE_RE.sub("", text, count=1)
