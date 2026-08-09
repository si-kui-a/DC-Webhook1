"""
scrapers/etf0050.py — 股市智投 (StockIntelli) 台股追蹤 + 法人買賣動向/板塊資金輪動。

股價/成交資料:從 stockintelli.com 個股頁面的 Next.js RSC payload 擷取
即時盤後資料。使用正則表達式直接從原始 HTML 提取，不需要 headless
browser 或 API key。

法人買賣動向/板塊資金輪動:TWSE官方「三大法人買賣超日報」(T86)端點,乾淨
JSON、免key(已直接curl驗證,非搜尋推測)。純結構化資料,不需要AI輔助，
跟財報/營收(twse_financials.py)同樣的設計原則。範圍限定WATCHLIST這12檔
既有追蹤股票(使用者確認)，板塊輪動用WATCHLIST既有的sector欄位聚合。
非交易日或T86抓取失敗時,法人/板塊區塊靜默省略,不影響既有股價追蹤功能。

撤資警示規則(使用者確認,規則式判斷不用AI,回答「是否要注意撤資」這個
具體問題,不只是丟原始數字):
- 個股:三大法人合計連續兩個交易日淨賣超(不論金額大小)才標記⚠️——
  跟nasdaq/sox(macro_fred.py)用同一套邏輯,偵測持續性撤資而非單日雜訊。
- 板塊:同板塊內過半數(WATCHLIST既有sector分組)成分股當日淨賣超即標記
  ⚠️「板塊性撤資」——只追蹤這12檔watchlist股票,不是全市場該產業。

均線支撐:TWSE官方「個股日成交資訊」(STOCK_DAY)端點,乾淨JSON、免key
(已直接curl驗證)。抓最近7個月歷史收盤價(約120+個交易日,足夠算MA120)
自己計算20/60/120日均線,現價落在均線±2%範圍內視為「測試支撐」——這是
計算,不是新資料源。每檔股票需分月抓取(TWSE此端點以月為單位),12檔
股票×7個月=84次請求,實測單股6個月約5秒，12檔預期約1分鐘內，非CPU密集
運算(不像cbc_digest的摘要運算，見PAT-07)，故未特別優化快取。
"""
import re
from collections import defaultdict
from datetime import timedelta
from datetime import date
from typing import Optional

import requests
from scrapers import http_client

SOURCE_ID = "stockintelli.tracking"
SOURCE_NAME = "股市智投-台股追蹤"
BASE_URL = "https://www.stockintelli.com"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date}&selectType=ALL"

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


def _fetch_institutional_flow(date_str: str) -> dict[str, dict] | None:
    """抓取三大法人買賣超日報(T86,TWSE官方JSON,免key),回傳
    {股票代號: {foreign, trust, dealer, total}}(單位:股)。非交易日/
    抓取失敗回傳None，不影響其餘既有股價追蹤功能。"""
    url = T86_URL.format(date=date_str)
    try:
        resp = http_client.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if data.get("stat") != "OK":
        return None

    result = {}
    for row in data.get("data", []):
        try:
            code = row[0]
            foreign = int(row[4].replace(",", ""))
            trust = int(row[10].replace(",", ""))
            dealer = int(row[11].replace(",", ""))
            total = int(row[18].replace(",", ""))
        except (ValueError, IndexError):
            continue
        result[code] = {"foreign": foreign, "trust": trust, "dealer": dealer, "total": total}
    return result


def _format_lots(shares: int) -> str:
    """股數轉張數(1張=1000股),含正負號箭頭。"""
    lots = shares / 1000
    arrow = "🔺" if lots > 0 else ("🔻" if lots < 0 else "▪")
    return f"{arrow}{lots:+,.0f}張"


def _fetch_recent_flows(max_lookback_days: int = 7) -> list[dict[str, dict]]:
    """往前找最近2個有效交易日的法人買賣超資料,由新到舊排序。T86在非
    交易日(週末/假日)回傳stat!=OK,自動往前跳過,不需要維護假日曆。"""
    results = []
    d = date.today()
    for _ in range(max_lookback_days):
        flow = _fetch_institutional_flow(d.strftime("%Y%m%d"))
        if flow:
            results.append(flow)
            if len(results) >= 2:
                break
        d -= timedelta(days=1)
    return results


STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date}&stockNo={code}"
MA_WINDOWS = (20, 60, 120)
MA_SUPPORT_BAND_PCT = 2.0  # 現價落在均線±2%範圍內視為「測試支撐」


def _fetch_closing_prices(code: str, months_back: int = 7) -> list[float]:
    """抓取個股最近N個月的日收盤價(TWSE STOCK_DAY,官方JSON免key),由舊到
    新排序。單月抓取失敗不中斷其餘月份(比照既有scraper容錯原則)。"""
    prices = []
    d = date.today().replace(day=1)
    months = []
    for _ in range(months_back):
        months.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    months.reverse()

    for month_start in months:
        url = STOCK_DAY_URL.format(date=month_start.strftime("%Y%m%d"), code=code)
        try:
            resp = http_client.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        if data.get("stat") != "OK":
            continue
        for row in data.get("data", []):
            try:
                prices.append(float(row[6].replace(",", "")))
            except (ValueError, IndexError):
                continue
    return prices


def _check_ma_support(closing_prices: list[float]) -> list[str]:
    """計算20/60/120日均線,回傳現價落在哪些均線±2%範圍內(視為測試支撐)
    的說明字串清單。資料不足以算某條均線就跳過該條,不報錯。"""
    if not closing_prices:
        return []
    current = closing_prices[-1]
    notes = []
    for window in MA_WINDOWS:
        if len(closing_prices) < window:
            continue
        ma = sum(closing_prices[-window:]) / window
        if ma == 0:
            continue
        deviation_pct = (current - ma) / ma * 100
        if abs(deviation_pct) <= MA_SUPPORT_BAND_PCT:
            notes.append(f"現價貼近{window}日均線({ma:.1f}，偏離{deviation_pct:+.1f}%)")
    return notes


def fetch() -> list[dict]:
    """抓取 watchlist 所有個股盤後資料+法人買賣動向+撤資警示+均線支撐，
    回傳為一篇「台股追蹤」摘要。"""
    today = date.today().isoformat()
    items = []
    recent_flows = _fetch_recent_flows()
    flow = recent_flows[0] if recent_flows else None
    prev_flow = recent_flows[1] if len(recent_flows) > 1 else None
    sector_totals: dict[str, int] = defaultdict(int)
    sector_sell_count: dict[str, int] = defaultdict(int)
    sector_stock_count: dict[str, int] = defaultdict(int)
    for _, _, s in WATCHLIST:
        sector_stock_count[s] += 1

    # Telegram只推警示重點(不推完整報告),見main.py的telegram_summary
    # 覆蓋機制;這裡收集「哪些股票/板塊觸發警示」供組裝Telegram專用摘要。
    stock_warnings: list[str] = []

    for code, name, sector in WATCHLIST:
        url = f"{BASE_URL}/stock/{code}"
        try:
            resp = http_client.get(url, headers=HEADERS, timeout=15)
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

        stock_flow = flow.get(code) if flow else None
        if stock_flow:
            summary += (
                f"\n法人買賣超：外資{_format_lots(stock_flow['foreign'])} "
                f"投信{_format_lots(stock_flow['trust'])} "
                f"自營商{_format_lots(stock_flow['dealer'])} "
                f"合計{_format_lots(stock_flow['total'])}"
            )
            sector_totals[sector] += stock_flow["total"]
            if stock_flow["total"] < 0:
                sector_sell_count[sector] += 1
                prev_stock_flow = prev_flow.get(code) if prev_flow else None
                if prev_stock_flow and prev_stock_flow["total"] < 0:
                    summary += "\n⚠️ 連續兩日遭三大法人淨賣超，建議留意"
                    stock_warnings.append(f"{name}（{code}）連續兩日遭三大法人淨賣超")

        closing_prices = _fetch_closing_prices(code)
        ma_notes = _check_ma_support(closing_prices)
        if ma_notes:
            summary += "\n📐 " + "；".join(ma_notes)

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

    sector_warnings: list[str] = []
    if sector_totals:
        lines.append("= 板塊資金輪動（三大法人合計，依既有追蹤股票聚合）=\n")
        for sector_name, total in sorted(sector_totals.items(), key=lambda kv: kv[1], reverse=True):
            is_majority_sell = sector_sell_count[sector_name] * 2 > sector_stock_count[sector_name]
            flag = " ⚠️ 板塊性撤資（過半成分股遭淨賣超）" if is_majority_sell else ""
            lines.append(f"- {sector_name}：{_format_lots(total)}{flag}")
            if is_majority_sell:
                sector_warnings.append(f"{sector_name}板塊性撤資（過半成分股遭淨賣超）")
        lines.append("")
    elif flow is None:
        lines.append("（今日非交易日或法人買賣超資料尚未公布，僅顯示股價追蹤）\n")

    report = "\n".join(lines).strip()

    item = {
        "title": f"台股權值股追蹤（{today}）",
        "summary": report,
        "url": BASE_URL,
        "published_at": today,
    }

    # Telegram只在有警示時推播,且只推警示重點(股票/板塊清單),不推完整
    # 12檔報告——完整內容留在Discord,Telegram是快速通知不是取代Discord
    # (使用者確認)。見main.py的telegram_summary覆蓋機制。
    all_warnings = stock_warnings + sector_warnings
    item["telegram_alert"] = bool(all_warnings)
    if all_warnings:
        item["telegram_summary"] = "⚠️ 今日撤資警示：\n" + "\n".join(f"- {w}" for w in all_warnings)

    return [item]


if __name__ == "__main__":
    import sys
    result = fetch()
    if result:
        sys.stdout.reconfigure(encoding="utf-8")
        print(result[0]["summary"])
    else:
        print("No data fetched")
