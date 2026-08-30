"""
scrapers/internship_518.py — 518熊班職缺搜尋(關鍵字:實習)。

實測(2026-07-31)確認：無CAPTCHA/反爬蟲擋門(跟1111不同)，職缺資料直接
伺服器渲染在搜尋結果頁的HTML裡，不像104有乾淨的JSON API，改用requests+
BeautifulSoup解析(div.job__card卡片，data-url屬性直接給職缺網址)。
一樣需要truststore.inject_into_ssl()(TLS憑證鏈「缺Subject Key
Identifier」問題，比照internship_mol.py)。

跟MOL/104一樣，是否為真正的實習職缺交給internship_util.is_relevant()
判斷，這裡只負責抓取+正規化欄位。
"""
import re
import time

import requests
import truststore
from bs4 import BeautifulSoup

truststore.inject_into_ssl()

SOURCE_ID = "internship.518.jobbank"
SOURCE_NAME = "518熊班(關鍵字:實習)"

SEARCH_URL = "https://www.518.com.tw/job-index-P-1.html"
SEARCH_KEYWORD = "實習"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15


def _get_with_retry(keyword: str) -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                SEARCH_URL, headers=HEADERS, params={"ad": keyword}, timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def _parse_salary_high(text: str) -> float | None:
    """只在明確標示「月薪」時才解析數字給高薪加分規則用——518同一個欄位
    可能是時薪/日薪/月薪混雜的自然語言文字，時薪/日薪數字遠小於月薪門檻
    (35000)，貿然解析容易誤用不同單位的數字。其餘一律回傳None(不加分，
    不是排除該職缺，只是不套用高薪加分)。"""
    if not text or "月薪" not in text:
        return None
    numbers = re.findall(r"[\d,]+", text)
    if not numbers:
        return None
    try:
        values = [float(n.replace(",", "")) for n in numbers]
        return max(values) if values else None
    except ValueError:
        return None


def fetch(keyword: str = SEARCH_KEYWORD) -> list[dict]:
    """回傳搜尋keyword的第一頁職缺(正規化欄位)。預設SEARCH_KEYWORD("實習")
    維持main.py既有排程行為不變；career_alignment.py會傳入target_role
    名稱做履歷對齊分析用的職缺搜尋(2026-08-30新增)。是否為真正的實習
    職缺交由main.py用internship_util.is_relevant()篩選，與MOL/104來源
    分工一致。"""
    html = _get_with_retry(keyword)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="job__card"):
        title_el = card.find("a", class_="job__title")
        if not title_el:
            continue

        url = card.get("data-url") or title_el.get("href", "")
        if not url:
            continue

        comp_el = card.find("span", class_="job__comp__name")
        salary_el = card.find("p", class_="job__salary")
        intro_el = card.find("p", class_="job__intro")
        date_el = card.find("span", class_="job__date")
        summaries = [li.get_text(strip=True) for li in card.select("ul.job__summaries li")]

        title_text = title_el.get_text(strip=True)
        comp_text = comp_el.get_text(strip=True) if comp_el else ""
        intro_text = intro_el.get_text(strip=True) if intro_el else ""
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        summary_parts = [p for p in [intro_text[:300], "、".join(summaries), salary_text] if p]

        items.append({
            "title": f"{comp_text} - {title_text}" if comp_text else title_text,
            "summary": "\n".join(summary_parts),
            "url": url,
            "published_at": date_el.get_text(strip=True) if date_el else None,
            "_filter_text": " ".join([title_text, intro_text]),
            "_salary_high": _parse_salary_high(salary_text),
        })
    return items


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    all_items = fetch()
    print(f"共 {len(all_items)} 筆原始職缺")
    for item in all_items[:5]:
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
