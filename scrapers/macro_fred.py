"""
scrapers/macro_fred.py — 總經指標追蹤(VIX/10年期公債殖利率/高收益債信用利差)。

資料源:FRED(美國聖路易聯邦儲備銀行)公開CSV端點,不需要API key、不需要
HTML解析,格式單純是「日期,數值」——比fed/tsmc那種靠selector抓HTML的
爬蟲更穩定(不會因網站改版而失效)。純結構化數字資料,不需要AI輔助。

推播頻率設計(使用者確認):
- Discord:每日固定推送(當日報告),不做閥值判斷。
- Telegram:只有任一指標觸發閥值(見_check_alert)才推送,避免每天洗版。
  透過item的"telegram_alert"欄位控制,main.py的run_source()讀取此欄位
  決定是否同步Telegram(預設True,向下相容既有來源——它們的raw item
  沒有這個key,.get("telegram_alert", True)一律視為要推)。

閥值(常見金融實務標準,已與使用者確認採用):
- VIX:絕對值>25(高恐慌區) 或 單日變動>15%
- 10年期公債殖利率:單日變動>10個基點(0.10 percentage point)
- 信用利差(高收益債OAS):單日變動>20個基點 或 絕對值>5%(壓力區)

title含日期,確保每天自然產生新的dedup_key(沿用etf0050.py的既有做法,
不需要另外的去重機制或db schema異動)。
"""
from datetime import date

import requests

SOURCE_ID = "fred.macro_indicators"
SOURCE_NAME = "總經指標追蹤"
BASE_URL = "https://fred.stlouisfed.org"

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# 依序:key -> (FRED系列代碼, 顯示名稱, 單位)
SERIES = {
    "vix": ("VIXCLS", "VIX恐慌指數", ""),
    "yield10y": ("DGS10", "10年期公債殖利率", "%"),
    "credit_spread": ("BAMLH0A0HYM2", "高收益債信用利差(OAS)", "%"),
}

HEADERS = {
    "User-Agent": (
        "IntelPusher/0.2 (personal research bot; "
        "contact: your-email@example.com)"
    ),
}


def _fetch_series(series_id: str) -> list[tuple[str, float]]:
    """回傳(日期,數值)列表,依日期由舊到新排序,已過濾缺值(FRED以'.'表示,
    通常是假日/資料延遲)。"""
    url = FRED_CSV_URL.format(series_id=series_id)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    rows = []
    for line in lines[1:]:  # 跳過header行(欄位名稱)
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, v = parts
        if v == ".":
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def _check_alert(key: str, latest: float, change: float) -> bool:
    """依SERIES key套用對應閥值規則,判斷是否觸發Telegram警示。"""
    if key == "vix":
        pct_change = (change / (latest - change) * 100) if (latest - change) else 0
        return latest > 25 or abs(pct_change) > 15
    if key == "yield10y":
        return abs(change) > 0.10
    if key == "credit_spread":
        return abs(change) > 0.20 or latest > 5
    return False


def fetch() -> list[dict]:
    """抓取三項總經指標最新值+日變動,組成一篇每日追蹤報告。任一指標觸發
    閥值就整篇標記telegram_alert=True(讓使用者看到完整脈絡,不只片段
    數字);單一指標抓取失敗不影響其餘兩項,該行顯示失敗原因。"""
    today = date.today().isoformat()
    lines = []
    any_alert = False
    any_success = False

    for key, (series_id, label, unit) in SERIES.items():
        try:
            rows = _fetch_series(series_id)
        except requests.RequestException as e:
            lines.append(f"- {label}：抓取失敗（{e}）")
            continue

        if len(rows) < 2:
            lines.append(f"- {label}：資料不足，無法比較日變動")
            continue

        any_success = True
        (_, prev), (latest_date, latest) = rows[-2], rows[-1]
        change = latest - prev
        alert = _check_alert(key, latest, change)
        any_alert = any_alert or alert

        arrow = "🔺" if change > 0 else ("🔻" if change < 0 else "▪")
        flag = " ⚠️" if alert else ""
        lines.append(
            f"- {label}：{latest}{unit} "
            f"{arrow}{change:+.2f}{unit}（{latest_date}）{flag}"
        )

    if not any_success:
        return []

    report = f"= 總經指標追蹤（{today}）=\n\n" + "\n".join(lines)

    return [{
        "title": f"總經指標追蹤（{today}）",
        "summary": report,
        "url": f"{BASE_URL}/series/{SERIES['vix'][0]}",
        "published_at": today,
        "telegram_alert": any_alert,
    }]


if __name__ == "__main__":
    import sys
    result = fetch()
    if result:
        sys.stdout.reconfigure(encoding="utf-8")
        print(result[0]["summary"])
        print("\ntelegram_alert:", result[0]["telegram_alert"])
    else:
        print("No data fetched")
