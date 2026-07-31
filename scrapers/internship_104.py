"""
scrapers/internship_104.py — 104人力銀行職缺搜尋(關鍵字:實習)。

實測(2026-07-31)找到目前真實的API端點：www.104.com.tw/jobs/search/api/jobs
(網路上流傳的舊教學文章寫的路徑.../jobs/search/list已經改版，實測回傳的是
HTML頁面殼而非JSON，改用Claude in Chrome看真實網路請求才找到現在這個)。
純requests呼叫，不需要Selenium/瀏覽器，只要帶Referer/Accept header就能
拿到JSON(已實測驗證，不是網路教學文章的二手資訊)。

104對「實習」關鍵字比對範圍很廣(單次搜尋顯示3萬8千多筆結果，不像MOL是
固定1000筆快照)，這裡固定抓「依最新排序」的前幾頁，搭配既有dedup機制
長期累積，不追求一次抓完全部——低頻率、低運算，比照MOL一天一次的節奏。

每筆職缺自帶labels欄位，其中"c@intern_welcome"是104自己標記的「歡迎
實習生」標籤，但連公益彩券門市兼職都可能帶這個標籤(不是嚴格的「這是
實習職缺」判斷)，所以不採用這個欄位做篩選，一律交給internship_util.
is_relevant()做關鍵字+規則式判斷，與MOL來源分工一致。
"""
import time
import urllib.parse

import requests

SOURCE_ID = "internship.104.jobbank"
SOURCE_NAME = "104人力銀行(關鍵字:實習)"

API_URL = "https://www.104.com.tw/jobs/search/api/jobs"
SEARCH_KEYWORD = "實習"
PAGES_PER_RUN = 3
PAGE_SIZE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    # HTTP header只能是latin-1可編碼字元，中文關鍵字須URL-encode後才能放進
    # Referer(2026-07-31實測踩過UnicodeEncodeError)。
    "Referer": f"https://www.104.com.tw/jobs/search/?keyword={urllib.parse.quote(SEARCH_KEYWORD)}",
    "Accept": "application/json, text/plain, */*",
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15


def _get_page_with_retry(page: int) -> dict | None:
    params = {
        "keyword": SEARCH_KEYWORD,
        "order": 15,  # 依最新排序(比照瀏覽器實測的預設排序值)
        "page": page,
        "pagesize": PAGE_SIZE,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(API_URL, headers=HEADERS, params=params, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def _build_title(record: dict) -> str:
    comp = (record.get("custName") or "").strip()
    name = (record.get("jobName") or "").strip()
    return f"{comp} - {name}" if comp else name or "（無標題職缺）"


def _build_summary(record: dict) -> str:
    parts = []
    desc = (record.get("description") or "").strip()
    if desc:
        parts.append(desc[:300])
    area = record.get("jobAddrNoDesc")
    if area:
        parts.append(f"地點：{area}")
    salary_lo = record.get("salaryLow")
    salary_hi = record.get("salaryHigh")
    if salary_lo or salary_hi:
        parts.append(f"薪資：{salary_lo}~{salary_hi}")
    return "\n".join(parts)


def _parse_salary_high(record: dict) -> float | None:
    """薪資上限供internship_util的高薪加分規則比對用，比照internship_mol.py
    的防呆寫法(104回傳的是數字型別，但一樣防範缺值/非數字的情況)。"""
    raw = record.get("salaryHigh")
    try:
        value = float(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def fetch() -> list[dict]:
    """回傳最近幾頁(依最新排序)搜尋「實習」的原始職缺(正規化欄位)。
    是否為真正的實習職缺由main.py用internship_util.is_relevant()篩選，
    與MOL來源分工一致。"""
    items = []
    for page in range(1, PAGES_PER_RUN + 1):
        data = _get_page_with_retry(page)
        if not data:
            break
        records = data.get("data", [])
        if not records:
            break
        for r in records:
            url = (r.get("link") or {}).get("job", "")
            if not url:
                continue
            items.append({
                "title": _build_title(r),
                "summary": _build_summary(r),
                "url": url,
                "published_at": r.get("appearDate"),
                "_filter_text": " ".join([r.get("jobName") or "", r.get("description") or ""]),
                "_salary_high": _parse_salary_high(r),
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
