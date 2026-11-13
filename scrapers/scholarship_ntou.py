"""
scrapers/scholarship_ntou.py — 國立臺灣海洋大學 生輔組「校外獎學金公告」。

列表頁 /p/403-1023-1039-N.php 每頁 12 筆（新到舊）；詳細頁是一張兩欄表格
（申請日期／申請方式／申請資格…），資格寫進 summary。沒有登入或驗證碼。
詳細頁只對資料庫還沒有的網址抓。
"""
import logging
import time

import requests
from bs4 import BeautifulSoup

import db

logger = logging.getLogger("scrapers.scholarship_ntou")

SOURCE_ID = "scholarship.ntou"
SOURCE_NAME = "海洋大學校外獎學金公告"

BASE_URL = "https://stu.ntou.edu.tw"
LIST_URL = BASE_URL + "/p/403-1023-1039-{page}.php?Lang=zh-tw"

MAX_PAGES = 3  # 新公告在最前面；更舊的是已過期項目
PAGE_DELAY_S = 1.0
RETRIES = 3
ELIGIBILITY_LABEL = "申請資格"
LABEL_MAX_LEN = 8  # 表格左欄的標籤都很短；長的是內文

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def parse_list(html: str) -> list[dict]:
    """列表頁 → [{title, url}]；同頁重複的網址只留一筆。"""
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href*='406-1023']"):
        title = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if not title or not href or href in seen:
            continue
        seen.add(href)
        items.append({"title": title, "url": href if href.startswith("http") else BASE_URL + href})
    return items


def parse_detail(html: str) -> dict[str, str]:
    """詳細頁表格：左欄短標籤 → 右欄內容。沒有表格回空 dict（資格不明）。"""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        if label and len(label) <= LABEL_MAX_LEN:
            fields[label] = cells[1].get_text(" ", strip=True)
    return fields


def _get(url: str) -> str | None:
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning("NTOU 抓取失敗 %s（第 %d 次）: %s", url, attempt + 1, e)
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)  # exponential backoff: 1 s, 2 s
    return None


def fetch() -> list[dict]:
    listed = []
    for page in range(1, MAX_PAGES + 1):
        html = _get(LIST_URL.format(page=page))
        items = parse_list(html) if html else []
        if not items:
            break
        listed.extend(items)
        time.sleep(PAGE_DELAY_S)

    known = db.known_urls(SOURCE_ID)
    results = []
    for i in listed:
        summary = None
        if i["url"] not in known:
            time.sleep(PAGE_DELAY_S)
            html = _get(i["url"])
            summary = (parse_detail(html) if html else {}).get(ELIGIBILITY_LABEL) or None
        results.append({"title": i["title"], "summary": summary, "url": i["url"], "published_at": None})
    logger.info("NTOU 抓取完成，共 %d 筆", len(results))
    return results


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
