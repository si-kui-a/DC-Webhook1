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
import requests
from bs4 import BeautifulSoup

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
    resp = requests.get(LIST_URL, headers=HEADERS, timeout=15)
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
