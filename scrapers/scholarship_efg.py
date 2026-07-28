"""
scrapers/scholarship_efg.py — European Funding Guide 歐洲獎學金資料庫。

Drupal Views table（class="views-table"），用 ?page=N GET 分頁。
實測約 91 頁（2275 筆），限制最多抓取 MAX_PAGES 頁避免請求過多。
"""
import logging
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("scrapers.scholarship_efg")

SOURCE_ID = "scholarship.efg"
SOURCE_NAME = "European Funding Guide（歐洲獎學金）"

BASE_URL = "https://www.european-funding-guide.eu"
LIST_PATH = "/scholarship/abroad"

MAX_PAGES = 5
PAGE_DELAY_S = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _extract_rows(soup: BeautifulSoup) -> list[dict]:
    items = []
    for row in soup.select("table.views-table tr"):
        link = row.select_one("td.views-field-title a")
        if not link:
            continue
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if not title or not href:
            continue
        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        items.append({
            "title": title,
            "url": full_url,
            "publishDate": None,
        })
    return items


def fetch() -> list[dict]:
    all_items = []

    for page in range(MAX_PAGES):
        url = f"{BASE_URL}{LIST_PATH}" if page == 0 else f"{BASE_URL}{LIST_PATH}?page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            logger.error("EFG 第 %d 頁抓取失敗: %s", page + 1, e)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = _extract_rows(soup)

        if not items:
            logger.info("EFG 第 %d 頁為空，停止", page + 1)
            break

        all_items.extend(items)

        if page < MAX_PAGES - 1:
            time.sleep(PAGE_DELAY_S)

    # 正規化欄位名稱
    results = [
        {"title": i["title"], "summary": None, "url": i["url"], "published_at": None}
        for i in all_items
    ]
    logger.info("EFG 抓取完成，共 %d 筆", len(results))
    return results


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
