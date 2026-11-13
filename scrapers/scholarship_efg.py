"""
scrapers/scholarship_efg.py — European Funding Guide 歐洲獎學金資料庫。

Drupal Views table（class="views-table"），用 ?page=N GET 分頁。
實測約 91 頁（2275 筆），限制最多抓取 MAX_PAGES 頁避免請求過多。
"""
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

import db

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


def parse_eligibility(html: str) -> dict[str, str]:
    """詳細頁「Who can apply」下的 <li><strong>標籤:</strong> 內容，轉成 {小寫標籤: 內容}。
    頁面沒有這一段（版型不同）回空 dict，呼叫端視為資格不明。"""
    soup = BeautifulSoup(html, "html.parser")
    ul = None
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True).lower() == "who can apply":
            ul = h2.find_next_sibling("ul")
            break
    fields = {}
    for li in (ul.find_all("li") if ul else []):
        label = li.find("strong")
        if not label:
            continue
        name = label.get_text(strip=True).rstrip(":").lower()
        label.extract()
        fields[name] = li.get_text(" ", strip=True)
    return fields


_ANY_NATIONALITY = re.compile(r"\b(all|any|every)\b|worldwide|international", re.I)
_BACHELOR = re.compile(r"bachelor|undergraduate|first[- ]year|school|pupil|any (degree|level)|all (degree|level)", re.I)
_POSTGRAD = re.compile(r"master|msc\b|\bma\b|phd|doctor|postgraduate|postdoc|mba|llm", re.I)
_LEVEL_LABELS = ("degree level", "who it is for", "level", "degree")


def is_eligible(fields: dict[str, str], profile: dict) -> bool:
    """依詳細頁文字粗判；資訊缺漏或看不懂一律放行（保守），只擋明確不符：
    國籍欄列了特定國家且不含我方國籍、學位欄只提到碩博不提學士。"""
    if not fields:
        return True
    nat = fields.get("nationality", "")
    country = (profile.get("country") or "Taiwan").lower()
    if nat and not _ANY_NATIONALITY.search(nat) and country not in nat.lower():
        return False
    level = " ".join(v for k, v in fields.items() if k in _LEVEL_LABELS)
    if level and _POSTGRAD.search(level) and not _BACHELOR.search(level):
        return False
    return True


def _fetch_detail(url: str) -> dict[str, str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("EFG 詳細頁抓取失敗 %s: %s", url, e)
        return {}
    return parse_eligibility(resp.text)


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

    # 詳細頁只對資料庫還沒有的項目抓（已抓過的不再重複請求）；
    # 資格寫進 summary，明確不符的在這裡就濾掉。
    from scrapers import scholarship_util
    profile = scholarship_util.load_profile()
    known = db.known_urls(SOURCE_ID)
    results = []
    for i in all_items:
        fields = {}
        if i["url"] not in known:
            time.sleep(PAGE_DELAY_S)
            fields = _fetch_detail(i["url"])
            if not is_eligible(fields, profile):
                continue
        summary = "；".join(f"{k}: {v}" for k, v in fields.items()) or None
        results.append({"title": i["title"], "summary": summary, "url": i["url"], "published_at": None})
    logger.info("EFG 抓取完成，共 %d 筆", len(results))
    return results


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
