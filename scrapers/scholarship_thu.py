"""
scrapers/scholarship_thu.py — 東海大學獎助學金查詢。

單頁 table#scholarship，154 筆在同一頁無分頁。
每列第二個 Scholarship_detail.php 連結為真正的獎學金名稱。
"""
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("scrapers.scholarship_thu")

SOURCE_ID = "scholarship.thu"
SOURCE_NAME = "東海大學獎助學金"

LIST_URL = "http://fsis.thu.edu.tw/wwwstud/frontend/Scholarship.php"

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
    table = soup.select_one("#scholarship")
    if not table:
        logger.warning("THU: 找不到 table#scholarship")
        return results

    rows = table.select("tr")
    for i, row in enumerate(rows):
        if i == 0:
            continue  # 表頭列
        links = row.select('a[href^="Scholarship_detail.php"]')
        if len(links) < 2:
            continue  # 沒有兩個連結就不是正常資料列
        name_link = links[1]  # 第二個連結才是獎學金名稱
        title = name_link.get_text(strip=True)
        href = name_link.get("href", "")
        if not title or not href:
            continue
        full_url = urljoin(LIST_URL, href)
        results.append({
            "title": title,
            "summary": None,
            "url": full_url,
            "published_at": None,
        })

    logger.info("THU 抓取完成，共 %d 筆", len(results))
    return results


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
