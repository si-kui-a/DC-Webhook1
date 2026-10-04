"""
scrapers/internship_gift.py — GIFT全球專業實習聯盟平臺(教育部指導跨校聯盟)。

實測(2026-07-31)：GET查詢字串即可(www.gift.org.tw/index.php/welcome/jobs
?skeyword=實習)，無CAPTCHA(頁面裡有captcha字樣，實測確認只是「忘記密碼」
跟頁尾聯絡表單用的圖形驗證碼，不是擋內容瀏覽的機制)。伺服器渲染HTML，
用BeautifulSoup解析div.job_box卡片。

這個平台沒有per-job的獨立網址——職缺詳細內容是用onclick="loadvacdatamore
(id)"透過POST(/index.php/welcome/company_job_dialog)動態載入進彈窗，
不是獨立頁面。因此url欄位改用「帶公司名稱關鍵字的搜尋頁連結」，讓使用者
點進去能重新搜到這筆(不是完美的deep link，是這個平台架構本身的限制)。

規模不大(實測「實習」關鍵字約2-3頁，每頁10筆)，固定抓前幾頁。
"""
import time
import urllib.parse

import requests
import truststore
from bs4 import BeautifulSoup

truststore.inject_into_ssl()

SOURCE_ID = "internship.gift.org"
SOURCE_NAME = "GIFT全球專業實習聯盟平臺"

SEARCH_URL = "https://www.gift.org.tw/index.php/welcome/jobs"
SEARCH_KEYWORD = "實習"
PAGES_PER_RUN = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15


def _get_page_with_retry(page: int, keyword: str) -> str | None:
    # 2026-10-04: the site moved pagination from /jobs/<n> (now 404) to ?page=<n>;
    # the old path was why GIFT was dropped from the schedule on 2026-09-24.
    params = {"skeyword": keyword, "s_1": "", "s_2": "", "s_3": "", "s_4": ""}
    if page > 1:
        params["page"] = str(page)
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                SEARCH_URL, headers=HEADERS,
                params=params,
                timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def _search_fallback_url(company: str, keyword: str) -> str:
    """這個平台沒有per-job的獨立網址，改連到帶公司名稱關鍵字的搜尋頁，
    見模組docstring。"""
    params = urllib.parse.urlencode({"skeyword": company or keyword})
    return f"{SEARCH_URL}?{params}"


def fetch(keyword: str = SEARCH_KEYWORD) -> list[dict]:
    """回傳最近幾頁搜尋keyword的職缺(正規化欄位)。預設SEARCH_KEYWORD("實習")
    維持main.py既有排程行為不變；career_alignment.py會傳入target_role
    名稱做履歷對齊分析用的職缺搜尋(2026-08-30新增)。是否為真正的實習
    職缺交由main.py用internship_util.is_relevant()篩選，與其餘來源分工
    一致。"""
    items = []
    for page in range(1, PAGES_PER_RUN + 1):
        html = _get_page_with_retry(page, keyword)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="job_box")
        if not cards:
            break

        for card in cards:
            title_el = card.select_one("h3.sub_title a")
            comp_el = card.select_one("div.job_text > a")
            if not title_el:
                continue

            title_text = title_el.get_text(strip=True)
            comp_text = comp_el.get_text(strip=True) if comp_el else ""
            info_texts = [p.get_text(" ", strip=True) for p in card.select("p.job_title")]

            items.append({
                "title": f"{comp_text} - {title_text}" if comp_text else title_text,
                "summary": "\n".join(info_texts),
                "url": _search_fallback_url(comp_text, keyword),
                "published_at": None,
                "_filter_text": " ".join([title_text] + info_texts),
                "_salary_high": None,  # 這個平台不列薪資數字，無法套用高薪加分規則
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
