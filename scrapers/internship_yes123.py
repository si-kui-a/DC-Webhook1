"""
scrapers/internship_yes123.py — yes123求職網職缺搜尋(關鍵字:實習)。

實測(2026-07-31)：無CAPTCHA，但搜尋是傳統ASP表單POST(不是JSON API)，
表單本身有兩百多個隱藏欄位(供進階篩選用)，實測只送最關鍵的find_key1
(關鍵字欄位)就足夠正確觸發搜尋，其餘欄位可以省略。

重要防雷：這個網站的回應沒有在Content-Type header正確宣告charset，
requests會誤判成ISO-8859-1，必須手動設定resp.encoding='utf-8'，否則
中文全部變亂碼且關鍵字比對永遠不會命中(2026-07-31實測踩過)。

伺服器渲染HTML(用BeautifulSoup解析)，職缺卡片只有標題/公司/薪資/地點/
work_type，沒有像MOL/104/518那樣的職缺描述摘要，_filter_text只能靠
標題本身判斷。
"""
import time

import requests
import truststore
from bs4 import BeautifulSoup

truststore.inject_into_ssl()

SOURCE_ID = "internship.yes123.jobbank"
SOURCE_NAME = "yes123求職網(關鍵字:實習)"

SEARCH_URL = "https://www.yes123.com.tw/wk_index/joblist.asp"
BASE_URL = "https://www.yes123.com.tw/wk_index/"
SEARCH_KEYWORD = "實習"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.yes123.com.tw/",
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15


def _get_with_retry() -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                SEARCH_URL, headers=HEADERS, data={"find_key1": SEARCH_KEYWORD}, timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"  # 見模組docstring：網站沒宣告charset，requests會誤判
            return resp.text
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def fetch() -> list[dict]:
    """回傳搜尋「實習」的第一頁職缺(正規化欄位)。是否為真正的實習職缺
    交由main.py用internship_util.is_relevant()篩選，與其餘來源分工一致。"""
    html = _get_with_retry()
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="Job_opening_item"):
        title_el = card.select_one(".Job_opening_item_title h5 a")
        if not title_el:
            continue
        href = title_el.get("href", "")
        if not href:
            continue

        comp_el = card.select_one(".Job_opening_item_title h6 a")
        salary_el = card.select_one(".Job_opening_item_title_payment span")
        info_spans = [s.get_text(strip=True) for s in card.select(".Job_opening_item_info > div > span")]

        title_text = title_el.get_text(strip=True)
        comp_text = comp_el.get_text(strip=True) if comp_el else ""
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        summary_parts = [p for p in [salary_text, "、".join(info_spans)] if p]

        items.append({
            "title": f"{comp_text} - {title_text}" if comp_text else title_text,
            "summary": "\n".join(summary_parts),
            "url": BASE_URL + href,
            "published_at": None,  # 列表頁只有月/日(如07.29)，沒有可靠年份資訊，不採用
            "_filter_text": title_text,
            "_salary_high": None,  # 列表頁薪資是自然語言文字(時薪/月薪/日薪混雜)，比照518的保守作法，這裡先不解析
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
