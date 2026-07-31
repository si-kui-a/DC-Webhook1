"""
scrapers/internship_mol.py — 台灣就業通(勞動部)全國職缺開放資料。

官方開放資料(data.gov.tw/dataset/44062)，免key，無robots限制(已直接驗證)。
需要truststore.inject_into_ssl()——同PAT-01(cbc.gov.tw)一樣的TWCA憑證鏈
「缺Subject Key Identifier」問題，政府網站常見，不是本站特例。

API本身不支援伺服器端關鍵字/offset篩選(已實測limit/offset/keyword參數皆
無效)，固定回傳最新1000筆全國職缺快照，重複呼叫offset=1000回傳0筆，代表
單次資料集上限就是1000筆。是否為實習職缺留給main.py用internship_util計分
判斷(比照scholarship_*.py只負責fetch原始資料、過濾邏輯統一在main.py的既有
分工模式)，這裡只負責抓取+正規化欄位。

2026-07-30實測：1000筆中約1%(約16筆)命中「實習」相關關鍵字，命中率不高，
需要靠dedup_key機制長期累積、每日多次執行才會有穩定的量。

2026-07-30實測發現這個API不穩定(3次測試中2次逾時/連線中斷)，已加上指數
退避重試(比照price_feed.py的既有模式)，非本站特例、單純政府API常見問題。
"""
import time

import requests
import truststore

truststore.inject_into_ssl()

SOURCE_ID = "internship.mol.taiwanjobs"
SOURCE_NAME = "台灣就業通(勞動部)全國職缺"

API_URL = "https://apiservice.mol.gov.tw/OdService/rest/datastore/A17000000J-030144-VAL"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 30


def _get_with_retry() -> dict | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    return None  # pragma: no cover


def _build_title(record: dict) -> str:
    """OCCU_DESC(職務名稱)常為空字串,此時退回用JOB_DETAIL開頭代替，
    避免title為空字串導致dedup_key失去區分度。"""
    occu = (record.get("OCCU_DESC（職務名稱）") or "").strip()
    comp = (record.get("COMPNAME（公司名稱）") or "").strip()
    if occu:
        return f"{comp} - {occu}" if comp else occu
    detail = (record.get("JOB_DETAIL（工作內容）") or "").strip()
    return f"{comp} - {detail[:30]}" if comp else detail[:40] or "（無標題職缺）"


def _build_summary(record: dict) -> str:
    parts = []
    detail = (record.get("JOB_DETAIL（工作內容）") or "").strip()
    if detail:
        parts.append(detail[:300])
    city = record.get("CITYNAME（工作地點）")
    if city:
        parts.append(f"地點：{city}")
    salary_lo = record.get("NT_L（薪資範圍下限）")
    salary_hi = record.get("NT_U（薪資範圍上限）")
    if salary_lo or salary_hi:
        parts.append(f"薪資：{salary_lo}~{salary_hi}")
    stop_date = record.get("STOP_DATE（應徵截止日期）")
    if stop_date:
        parts.append(f"截止日：{stop_date}")
    return "\n".join(parts)


def _parse_salary_high(record: dict) -> float | None:
    """薪資上限(NT_U)供internship_util的高薪加分規則比對用(2026-07-31新增)。
    原始欄位是字串,空值/非數字一律視為無資料,不影響其餘篩選邏輯。"""
    raw = record.get("NT_U（薪資範圍上限）")
    try:
        value = float(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def fetch() -> list[dict]:
    """回傳全部1000筆原始職缺(正規化欄位)，是否為實習職缺由main.py用
    internship_util.is_relevant()篩選，與scholarship_*.py的既有分工一致。"""
    data = _get_with_retry()
    if not data or not data.get("success"):
        return []

    records = data.get("result", {}).get("records", [])
    items = []
    for r in records:
        url = (r.get("URL_QUERY（職缺資料URL）") or "").strip()
        if not url:
            continue
        items.append({
            "title": _build_title(r),
            "summary": _build_summary(r),
            "url": url,
            "published_at": r.get("TRANDATE（職缺更新日期）"),
            # 供main.py關鍵字篩選用的原始欄位，不進db.item表(那裡只存title/summary)。
            "_filter_text": " ".join([
                r.get("OCCU_DESC（職務名稱）") or "",
                r.get("JOB_DETAIL（工作內容）") or "",
                r.get("CJOB_NAME2（職務小類別名稱）") or "",
            ]),
            # 供main.py高薪加分規則用的原始數字欄位，同樣不進db.item表。
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
