"""
scrapers/internship_rich.py — 教育部青年署RICH職場體驗網(政府見習/工讀計畫)。

實測(2026-07-31)：POST呼叫rich.yda.gov.tw/api/job/job001/search，純
requests即可(無CAPTCHA)。用Claude in Chrome攔截頁面的XHR才找到真正的
payload欄位結構(keyword/prj_category/city/com_type/cmpy_code/order_by/
_page)，網路上沒有教學文章可參考(這個平台冷門)。

這個平台整體只有十幾筆職缺(規模遠小於MOL/104)，且官方用語是「見習/
工讀」不是「實習」——關鍵字搜尋"實習"/"見習"都查不到任何一筆(可能keyword
欄位比對的是特定內部欄位而非公開的敘述文字)，改成關鍵字留空直接抓
全部。平台本身定位就是見習/工讀媒合，不需要再用「實習」關鍵字門檻判斷
是不是實習——main.py::run_internship()對這個來源改用
internship_util.passes_profile_filters()(跳過關鍵字計分，只套用學校/
年級/國籍/身份別/科系/行業別排除)。
"""
import time

import requests
import truststore

truststore.inject_into_ssl()

SOURCE_ID = "internship.rich.yda"
SOURCE_NAME = "RICH職場體驗網(教育部青年署)"

API_URL = "https://rich.yda.gov.tw/api/job/job001/search"
PAGE_SIZE = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Referer": "https://rich.yda.gov.tw/job/job-search",
    "Origin": "https://rich.yda.gov.tw",
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15


def _get_with_retry() -> dict | None:
    payload = {
        "keyword": "",
        "prj_category": [],
        "city": [],
        "com_type": [],
        "cmpy_code": "",
        "order_by": "post_start_date",
        "_page": {"pageno": 1, "size": PAGE_SIZE},
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def _build_summary(record: dict) -> str:
    parts = []
    desc = (record.get("job_desc") or "").strip()
    if desc:
        parts.append(desc[:300])
    prj = (record.get("prj_name") or "").strip()
    if prj:
        parts.append(f"計畫：{prj}")
    salary_type = record.get("salary_type")
    salary_lo = record.get("salary_min")
    salary_hi = record.get("salary_max")
    if salary_lo or salary_hi:
        label = {"Monthly": "月薪", "Hourly": "時薪"}.get(salary_type, "薪資")
        parts.append(f"{label}：{salary_lo}~{salary_hi}")
    return "\n".join(parts)


def _parse_salary_high(record: dict) -> float | None:
    """只在明確是月薪(salary_type=='Monthly')時才給高薪加分規則用——時薪
    數字遠小於月薪門檻，混用會誤判，比照internship_518.py的防呆邏輯。"""
    if record.get("salary_type") != "Monthly":
        return None
    raw = record.get("salary_max")
    try:
        value = float(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def fetch() -> list[dict]:
    """回傳RICH平台目前全部職缺(正規化欄位)。這個來源整體規模很小(通常
    十幾筆)，不分頁篩選，直接抓全部。是否符合使用者個人條件交由main.py
    用internship_util.passes_profile_filters()判斷(跳過關鍵字門檻，只套
    用學校/年級/國籍/身份別/科系/行業別排除)。"""
    data = _get_with_retry()
    if not data or data.get("header", {}).get("code") != "OK":
        return []

    records = data.get("body", {}).get("data", [])
    items = []
    for r in records:
        job_no = r.get("job_no")
        if not job_no:
            continue
        title = f"{r.get('com_name', '')} - {r.get('job_title', '')}".strip(" -")
        items.append({
            "title": title or "（無標題職缺）",
            "summary": _build_summary(r),
            "url": f"https://rich.yda.gov.tw/job/detail/{job_no}",
            "published_at": r.get("post_start_date"),
            "_filter_text": " ".join([r.get("job_title") or "", r.get("job_desc") or ""]),
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
