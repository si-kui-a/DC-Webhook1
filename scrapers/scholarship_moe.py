"""
scrapers/scholarship_moe.py — 教育部圓夢助學網。

兩個分類（民間團體獎助學金、政府機關獎助學金），各自用 ASP.NET WebForms
GridView 渲染清單。分頁靠 __doPostBack，需帶 __VIEWSTATE/__EVENTVALIDATION
POST 回同一頁。目前兩個分類都是單頁無分頁，但保留分頁邏輯備用。
"""
import logging
import time
import re

import requests
import truststore
from bs4 import BeautifulSoup

# 教育部網站 SSL 憑證缺少 Subject Key Identifier，預設 certifi 驗證會失敗。
# 比照 main.py 用系統信任庫取代 certifi。此處獨立 inject 讓本檔案也能直接測試。
truststore.inject_into_ssl()

logger = logging.getLogger("scrapers.scholarship_moe")

SOURCE_ID = "scholarship.moe"
SOURCE_NAME = "教育部圓夢助學網"

BASE_URL = "https://www.edu.tw/helpdreams/"

CATEGORIES = [
    {"label": "民間團體獎助學金", "n": "2BBF7170197CE7D3", "sms": "0A01A72AAB9E5CD4"},
    {"label": "政府機關獎助學金", "n": "11EFF33070D6DF4B", "sms": "931FF851D2FB2128"},
]

MAX_PAGES_PER_CATEGORY = 5
PAGE_DELAY_S = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _category_url(cat: dict) -> str:
    return f"{BASE_URL}Grants.aspx?n={cat['n']}&sms={cat['sms']}"


def _extract_rows(soup: BeautifulSoup) -> list[dict]:
    """從 GridView table 提取當頁的公告列表。"""
    items = []
    table = soup.select_one("#ContentPlaceHolder1_gvIndex")
    if not table:
        return items

    for row in table.select("tr"):
        link = row.select_one("a.css_mark")
        if not link:
            continue
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if not title or not href:
            continue
        # href 可能是相對路徑
        if href.startswith("/"):
            href = f"https://www.edu.tw{href}"
        elif not href.startswith("http"):
            href = f"{BASE_URL}{href}"
        items.append({
            "title": title,
            "url": href,
            "publishDate": None,
        })
    return items


def _find_pager(soup: BeautifulSoup, target_page: int) -> dict | None:
    """從頁面中找出往 target_page 的 __doPostBack 資訊。"""
    for a in soup.select("a[href*='__doPostBack']"):
        href = a.get("href", "")
        m = re.search(r"__doPostBack\('([^']+)'\s*,\s*'Page\$(\d+)'\)", href)
        if m and int(m.group(2)) == target_page:
            return {"eventTarget": m.group(1), "eventArgument": f"Page${target_page}"}
    return None


def _collect_hidden(soup: BeautifulSoup) -> dict:
    """收集頁面中所有 hidden input，用於 POST back。"""
    fields = {}
    for inp in soup.select("input[type=hidden]"):
        name = inp.get("name")
        if name:
            fields[name] = inp.get("value", "")
    return fields


def _fetch_one_category(cat: dict) -> list[dict]:
    """抓取單一分類下所有頁面的公告列表。"""
    url = _category_url(cat)
    all_items = []

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    all_items.extend(_extract_rows(soup))

    page = 1
    while page < MAX_PAGES_PER_CATEGORY:
        pager = _find_pager(soup, page + 1)
        if not pager:
            break  # 沒有分頁

        time.sleep(PAGE_DELAY_S)

        hidden = _collect_hidden(soup)
        form_data = {
            **hidden,
            "__EVENTTARGET": pager["eventTarget"],
            "__EVENTARGUMENT": pager["eventArgument"],
        }
        resp = requests.post(url, data=form_data, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        next_items = _extract_rows(soup)
        if not next_items:
            break
        all_items.extend(next_items)
        page += 1

    return all_items


def fetch() -> list[dict]:
    all_items = []
    for cat in CATEGORIES:
        try:
            items = _fetch_one_category(cat)
            logger.info("MOE [%s] 抓到 %d 筆", cat["label"], len(items))
            all_items.extend(items)
        except Exception as e:
            logger.error("MOE [%s] 抓取失敗: %s", cat["label"], e)
        time.sleep(PAGE_DELAY_S)
    # 正規化欄位名稱（與 intel-pusher 格式一致）
    return [
        {"title": i["title"], "summary": None, "url": i["url"], "published_at": None}
        for i in all_items
    ]


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
