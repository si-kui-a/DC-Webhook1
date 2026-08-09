"""
scrapers/twse_financials.py — 台積電/中華電 財報＋月營收（TWSE OpenAPI 官方開放資料）。

已驗證(直接呼叫官方API,非搜尋推測):
- 月營收: https://openapi.twse.com.tw/v1/opendata/t187ap05_L (乾淨JSON,免key,
  依「公司代號」欄位篩選即可,涵蓋全部上市公司)
- 綜合損益表(季報): https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci
  (「一般業」類別,台積電、中華電皆屬此類,已用真實資料驗證)
- 資產負債表(季報): https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci
- 法說會逐字稿: 已評估放棄——TWSE官方無結構化資料,MOPS網頁與台積電/
  StatementDog官網皆有反爬蟲防護(curl實測403),不在此模組範圍內。

純事實陳述格式(不經AI敘事生成)——這是數字快照,沒有「觀點」可比對,套用
AI交叉比對格式沒有意義,詳見設計討論。dedup比照其餘來源走db.py既有的
title+url機制,title用「公司+期別」組成,同一期別只會被推播一次。
"""
from datetime import date

import requests
from scrapers import http_client

REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
BALANCE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"

# MOPS個股資訊頁,作為附上的參考連結(非資料來源本身,單純方便使用者回頭查證)
MOPS_COMPANY_URL = "https://mops.twse.com.tw/mops/web/t05st03?firstin=1&co_id={code}"

HEADERS = {
    "User-Agent": "IntelPusher/0.2 (personal research bot; contact: your-email@example.com)"
}


def _fetch_json(url: str) -> list[dict]:
    resp = http_client.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _find_company_row(rows: list[dict], code: str) -> dict | None:
    for row in rows:
        if row.get("公司代號") == code:
            return row
    return None


def _pct(val: str) -> str:
    try:
        n = float(val)
        sign = "+" if n > 0 else ""
        return f"{sign}{n:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _yi(val: str) -> str:
    """千元轉億元,四捨五入到小數點後1位(TWSE財報/營收數字單位皆為千元)。"""
    try:
        return f"{float(val) / 100_000:.1f}億"
    except (TypeError, ValueError):
        return "N/A"


def _format_revenue_block(row: dict) -> str:
    # 資料年月為民國年+月組成,如"11506"=民國115年06月,不是西元年,長度5碼
    # (民國年3碼+月2碼),不能假設固定6碼切法。
    period = row.get("資料年月", "")
    year = period[:-2] if len(period) > 2 else period
    month = period[-2:] if len(period) > 2 else ""
    return (
        f"📊 民國{year}年{month}月營收\n"
        f"- 當月營收：{_yi(row.get('營業收入-當月營收'))}"
        f"（月增 {_pct(row.get('營業收入-上月比較增減(%)'))}，"
        f"年增 {_pct(row.get('營業收入-去年同月增減(%)'))}）\n"
        f"- 累計營收：{_yi(row.get('累計營業收入-當月累計營收'))}"
        f"（年增 {_pct(row.get('累計營業收入-前期比較增減(%)'))}）"
    )


def _format_income_block(row: dict) -> str:
    year = row.get("年度", "")
    quarter = row.get("季別", "")
    eps = row.get("基本每股盈餘（元）", "N/A")
    return (
        f"📈 {year}年Q{quarter}財報\n"
        f"- 營業收入：{_yi(row.get('營業收入'))}\n"
        f"- 營業毛利：{_yi(row.get('營業毛利（毛損）淨額'))}\n"
        f"- 營業利益：{_yi(row.get('營業利益（損失）'))}\n"
        f"- 本期淨利：{_yi(row.get('本期淨利（淨損）'))}\n"
        f"- 每股盈餘：{eps} 元"
    )


def _fetch(code: str, name: str) -> list[dict]:
    today = date.today().isoformat()
    ref_url = MOPS_COMPANY_URL.format(code=code)
    items = []

    try:
        revenue_rows = _fetch_json(REVENUE_URL)
        rev_row = _find_company_row(revenue_rows, code)
        if rev_row:
            period = rev_row.get("資料年月", "")
            items.append({
                "title": f"{name} {period}月營收",
                "summary": _format_revenue_block(rev_row),
                "url": ref_url,
                "published_at": rev_row.get("出表日期", today),
                "telegram_alert": False,
            })
    except requests.RequestException as e:
        items.append({
            "title": f"{name} 月營收抓取失敗",
            "summary": f"無法取得TWSE月營收資料：{e}",
            "url": ref_url,
            "published_at": today,
            "telegram_alert": False,
        })

    try:
        income_rows = _fetch_json(INCOME_URL)
        inc_row = _find_company_row(income_rows, code)
        if inc_row:
            year = inc_row.get("年度", "")
            quarter = inc_row.get("季別", "")
            items.append({
                "title": f"{name} {year}年Q{quarter}財報",
                "summary": _format_income_block(inc_row),
                "url": ref_url,
                "published_at": inc_row.get("出表日期", today),
                "telegram_alert": False,
            })
    except requests.RequestException as e:
        items.append({
            "title": f"{name} 財報抓取失敗",
            "summary": f"無法取得TWSE財報資料：{e}",
            "url": ref_url,
            "published_at": today,
            "telegram_alert": False,
        })

    return items


def fetch_tsmc() -> list[dict]:
    return _fetch("2330", "台積電")


def fetch_chunghwa() -> list[dict]:
    return _fetch("2412", "中華電")


SOURCE_ID_TSMC = "twse.financials.2330"
SOURCE_NAME_TSMC = "台積電財報/營收"
SOURCE_ID_CHUNGHWA = "twse.financials.2412"
SOURCE_NAME_CHUNGHWA = "中華電財報/營收"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for label, fn in (("台積電", fetch_tsmc), ("中華電", fetch_chunghwa)):
        print(f"=== {label} ===")
        for item in fn():
            print(f"[{item['title']}]")
            print(item["summary"])
            print()
