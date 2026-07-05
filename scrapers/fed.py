"""
scrapers/fed.py — 美國聯準會（Fed）FOMC 新聞稿。

已驗證來源（直接 curl 確認，非搜尋推測）：
- 當年度 FOMC 專頁：https://www.federalreserve.gov/newsevents/pressreleases/{year}-press-fomc.htm
  伺服器端渲染，確認含 <div class="eventlist__time"><time>...</time></div> 與
  <div class="eventlist__event"><p><a href="...">標題</a></p></div> 成對出現（curl 實測 7 筆）。
- 首頁 /newsevents/pressreleases.htm 本身清單是 JS 載入（curl 只拿到 <noscript> 提示），
  不要拿它當抓取目標；年度封存頁才是可用的。
- 年度 FOMC 頁本身已經只列 FOMC 相關項目，不需要再用 monetary*.htm 的 href pattern
  額外篩選（上一版用寬鬆 href 比對，抓得到標題但抓不到日期；改用 eventlist 結構後
  兩者都拿得到）。

這是本系統中結構最穩定的來源（.gov 官方頁、非 JS 動態渲染）。
"""
from datetime import date

import requests
from bs4 import BeautifulSoup

SOURCE_ID = "fed.fomc_press"
SOURCE_NAME = "美國聯準會-FOMC新聞稿"
BASE_URL_TEMPLATE = "https://www.federalreserve.gov/newsevents/pressreleases/{year}-press-fomc.htm"

HEADERS = {
    "User-Agent": "IntelPusher/0.1 (personal research bot; contact: your-email@example.com)"
}


def fetch() -> list[dict]:
    url = BASE_URL_TEMPLATE.format(year=date.today().year)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for time_el in soup.select(".eventlist__time"):
        row = time_el.find_parent("div", class_="row")
        if not row:
            continue
        event_el = row.select_one(".eventlist__event")
        link_el = event_el.find("a") if event_el else None
        if not link_el:
            continue
        href = link_el.get("href", "")
        full_url = href if href.startswith("http") else f"https://www.federalreserve.gov{href}"
        results.append({
            "title": link_el.get_text(strip=True),
            "summary": None,
            "url": full_url,
            "published_at": time_el.get_text(strip=True),
        })
    return results


def fetch_detail_text(url: str) -> str:
    """抓單則新聞稿詳情頁的正文全文（供摘要用）。

    已驗證的結構（curl 實測 monetary20260617a.htm）：#article 底下有兩個
    class 都是 "col-xs-12 col-sm-8 col-md-8" 的 div，第一個多帶一個
    "heading" class（標題/分享按鈕，不是正文），第二個才是真正的內文
    <p> 段落。用 :not(.heading) 排除第一個，避免抓到分享連結、社群按鈕
    等雜訊文字混進摘要。
    """
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    article = soup.select_one("#article")
    if not article:
        return ""
    body_div = article.select_one("div.col-xs-12.col-sm-8.col-md-8:not(.heading)")
    if not body_div:
        return ""
    paragraphs = body_div.find_all("p")
    return " ".join(p.get_text(strip=True) for p in paragraphs)
