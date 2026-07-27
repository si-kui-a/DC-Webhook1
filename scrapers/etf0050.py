"""
scrapers/etf0050.py — 股市智投 (StockIntelli) 台股追蹤。

從 stockintelli.com 個股頁面的 Next.js RSC payload 擷取即時盤後資料。
使用正則表達式直接從原始 HTML 提取，不需要 headless browser 或 API key。
"""
import re
from datetime import date
from typing import Optional

import requests

SOURCE_ID = "stockintelli.tracking"
SOURCE_NAME = "股市智投-台股追蹤"
BASE_URL = "https://www.stockintelli.com"

HEADERS = {
    "User-Agent": (
        "IntelPusher/0.2 (personal research bot; "
        "contact: your-email@example.com)"
    ),
}

# 主要權值股 watchlist（代碼, 名稱, 產業類別）
WATCHLIST: list[tuple[str, str, str]] = [
    ("2330", "台積電", "半導體"),
    ("2317", "鴻海", "其他電子"),
    ("2454", "聯發科", "半導體"),
    ("2308", "台達電", "電子零組件"),
    ("2412", "中華電", "通信網路"),
    ("2303", "聯電", "半導體"),
    ("2881", "富邦金", "金融"),
    ("2882", "國泰金", "金融"),
    ("3711", "日月光投控", "半導體"),
    ("2002", "中鋼", "鋼鐵"),
    ("1216", "統一", "食品"),
    ("1301", "台塑", "塑膠"),
]


def _extract_num(html: str, key: str) -> Optional[float]:
    """從 RSC payload 中擷取數值欄位。

    RSC payload 中的 JSON 使用 \\" 作為引號標記，如 \\"closing_price\\":2350。
    部分股票將數值以字串表示（如 \\"closing_price\\":\\"253.0\\"），一併處理。
    """
    # 支援兩種格式：":NUM" 或 ":\"NUM\""
    m = re.search(r'\\"{0}\\":(?:\\")?(-?[\d.]+)(?:\\")?'.format(key), html)
    if m:
        val = m.group(1)
        return float(val) if "." in val else int(val)
    return None


def _extract_str(html: str, key: str) -> Optional[str]:
    """從 RSC payload 中擷取字串欄位。"""
    m = re.search(r'\\"{0}\\":\\"([^"\\\\]+)\\"'.format(key), html)
    if m:
        return m.group(1)
    return None


def _extract_stock_data(html: str, stock_code: str) -> Optional[dict]:
    """從個股頁面 HTML 擷取完整盤後資料。"""
    cp = _extract_num(html, "closing_price")
    if cp is None:
        return None

    result = {
        "code": stock_code,
        "date": _extract_str(html, "date"),
        "closing_price": cp,
        "opening_price": _extract_num(html, "opening_price"),
        "high": _extract_num(html, "highest_price"),
        "low": _extract_num(html, "lowest_price"),
        "price_change": _extract_num(html, "price_change"),
        "price_change_pct": _extract_num(html, "price_change_percent"),
        "volume": _extract_num(html, "trade_volume"),
        "trade_value": _extract_num(html, "trade_value"),
        "volume_ratio": _extract_num(html, "volume_ratio"),
    }

    # 擷取財務健康資料（在同頁面但不同 RSC 區段）
    fh_idx = html.find("financial-health")
    if fh_idx >= 0:
        fh_section = html[fh_idx : fh_idx + 500]
        result["health_score"] = _extract_num(fh_section, "score")
        health_grade_m = re.search(r'\\"grade\\":\\"([A-Z+]+)\\"', fh_section)
        if health_grade_m:
            result["health_grade"] = health_grade_m.group(1)
        result["industry_rank"] = _extract_num(fh_section, "industry_rank")
        result["industry_total"] = _extract_num(fh_section, "industry_total")

    return result


def _format_change(pct: Optional[float]) -> str:
    """格式化漲跌幅（含箭頭）。"""
    if pct is None:
        return "N/A"
    if pct > 0:
        return f"+{pct}%"
    elif pct < 0:
        return f"{pct}%"
    return f"0%"


def _format_value(val: Optional[float]) -> str:
    """格式化成交值（億元）。"""
    if val is None:
        return "N/A"
    yi = val / 100_000_000
    return f"{yi:.1f}億"


def fetch() -> list[dict]:
    """抓取 watchlist 所有個股盤後資料，回傳為一篇「台股追蹤」摘要。"""
    today = date.today().isoformat()
    items = []

    for code, name, sector in WATCHLIST:
        url = f"{BASE_URL}/stock/{code}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            items.append({
                "title": f"{name}（{code}）— 抓取失敗",
                "summary": f"無法取得資料：{e}",
                "url": url,
                "published_at": today,
            })
            continue

        data = _extract_stock_data(resp.text, code)
        if not data:
            items.append({
                "title": f"{name}（{code}）— 解析失敗",
                "summary": "無法從頁面解析盤後資料（RSC 結構可能已變動）",
                "url": url,
                "published_at": today,
            })
            continue

        change_str = _format_change(data.get("price_change_pct"))
        close_price = data.get("closing_price") or "N/A"
        volume_str = _format_value(data.get("trade_value"))
        vol_ratio = data.get("volume_ratio") or "N/A"
        health_info = ""
        if data.get("health_grade"):
            hr = data.get("industry_rank", "?")
            ht = data.get("industry_total", "?")
            health_info = f"| 財務評級 {data['health_grade']}（同業 {hr}/{ht}）"

        summary = (
            f"收盤 {close_price}  {change_str}\n"
            f"成交值 {volume_str}  量比 {vol_ratio}{health_info}"
        )

        items.append({
            "title": f"{name}（{code}）— {sector}",
            "summary": summary,
            "url": url,
            "published_at": data.get("date", today),
        })

    if not items:
        return []

    # 組合成一篇統整報表
    lines = [f"= 台股權值股追蹤（{today}）=\n"]
    for item in items:
        lines.append(f"- {item['title']}")
        lines.append(f"  {item['summary']}")
        lines.append("")

    report = "\n".join(lines).strip()

    return [{
        "title": f"台股權值股追蹤（{today}）",
        "summary": report,
        "url": BASE_URL,
        "published_at": today,
    }]


if __name__ == "__main__":
    import sys
    result = fetch()
    if result:
        sys.stdout.reconfigure(encoding="utf-8")
        print(result[0]["summary"])
    else:
        print("No data fetched")
