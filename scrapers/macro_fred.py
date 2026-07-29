"""
scrapers/macro_fred.py — 總經指標追蹤(VIX/10年期公債殖利率/高收益債信用利差)。

資料源:FRED(美國聖路易聯邦儲備銀行)公開CSV端點,不需要API key、不需要
HTML解析,格式單純是「日期,數值」——比fed/tsmc那種靠selector抓HTML的
爬蟲更穩定(不會因網站改版而失效)。純結構化數字資料,不需要AI輔助。

推播頻率設計(使用者確認):
- Discord「總經指標追蹤」:每日固定推送(當日報告),不做閥值判斷。
- Discord「總經異常警報」(secondary_webhook_env機制,見main.py
  run_source()):只有任一指標觸發閥值才額外推送到這個獨立頻道，跟每日
  固定報告的頻道分開，避免警報訊息被每天的例行報告稀釋。
- Telegram:只有任一指標觸發閥值(見_check_alert)才推送,避免每天洗版。
  透過item的"telegram_alert"欄位控制,main.py的run_source()讀取此欄位
  決定是否同步Telegram(預設True,向下相容既有來源——它們的raw item
  沒有這個key,.get("telegram_alert", True)一律視為要推)。

閥值(常見金融實務標準,已與使用者確認採用):
- VIX:絕對值>25(高恐慌區) 或 單日變動>15%
- 10年期公債殖利率:單日變動>10個基點(0.10 percentage point)
- 信用利差(高收益債OAS):單日變動>20個基點 或 絕對值>5%(壓力區)
- 那斯達克綜合指數/費城半導體指數:連續兩日下跌(不論每日跌幅大小) 或
  單日跌幅>=1%(使用者確認,只偵測下跌方向的風險，不是雙向大波動)

淨流動性(WALCL-TGA-RRP,市場慣例公式,使用者確認採用):
- WALCL(Fed資產負債表總資產)、TGA(財政部一般帳戶餘額)在FRED上都是
  「每週三發布」的週頻資料,不是逐日變動——不管抓取頻率多高,實際數值
  一週只會變一次,「當日抓取」的意義是「當週三發布後最快當天偵測到」,
  不是每天都有全新數值(已與使用者說明並確認採用)。
- RRP(隔夜逆回購,RRPONTSYD)才是真正逐日更新。
- 結論句用規則式程式產生(不用AI,與本檔案其餘部分一致的設計原則):
  TGA上升=資金被抽離系統(緊縮)、TGA下降=資金釋出(寬鬆)，WALCL攀升/下滑
  同理，組合成一句話說明本週淨流動性增減方向與主因。

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
    "nasdaq": ("NASDAQCOM", "那斯達克綜合指數", ""),
    "sox": ("NASDAQSOX", "費城半導體指數(費半)", ""),
}

# 觸發警報時額外推播到獨立的「總經異常警報」頻道(main.py的run_source()
# 讀取item的secondary_webhook_env欄位決定)，跟每日固定的「總經指標追蹤」
# 頻道分開，避免警報被每天例行報告稀釋。
ALERT_WEBHOOK_ENV = "WEBHOOK_MACRO_ALERT"

# 淨流動性公式三項來源。WALCL/TGA原始單位為百萬美元,RRP原始單位為十億
# 美元,計算時統一換算成十億美元。
LIQUIDITY_SERIES = {
    "walcl": ("WALCL", "Fed資產負債表總資產(WALCL)"),
    "tga": ("WTREGEN", "財政部一般帳戶餘額(TGA)"),
    "rrp": ("RRPONTSYD", "隔夜逆回購(RRP)"),
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


def _check_alert(key: str, latest: float, change: float, rows: list[tuple[str, float]]) -> bool:
    """依SERIES key套用對應閥值規則,判斷是否觸發警報(Telegram+總經異常
    警報頻道)。nasdaq/sox需要看連續兩日的變動方向,故多傳入rows供比對。"""
    if key == "vix":
        pct_change = (change / (latest - change) * 100) if (latest - change) else 0
        return latest > 25 or abs(pct_change) > 15
    if key == "yield10y":
        return abs(change) > 0.10
    if key == "credit_spread":
        return abs(change) > 0.20 or latest > 5
    if key in ("nasdaq", "sox"):
        pct_change = (change / (latest - change) * 100) if (latest - change) else 0
        if pct_change <= -1:
            return True
        if len(rows) >= 3:
            prev_change = rows[-2][1] - rows[-3][1]
            if change < 0 and prev_change < 0:
                return True
        return False
    return False


def _fetch_liquidity() -> dict | None:
    """抓取WALCL/TGA/RRP,計算淨流動性(=WALCL-TGA-RRP,單位統一換算成
    十億美元)與週變動,規則式(不用AI)產生一句話結論。任一序列抓取失敗
    或資料不足就回傳None,不影響其餘總經指標照常顯示。"""
    try:
        walcl_rows = _fetch_series(LIQUIDITY_SERIES["walcl"][0])
        tga_rows = _fetch_series(LIQUIDITY_SERIES["tga"][0])
        rrp_rows = _fetch_series(LIQUIDITY_SERIES["rrp"][0])
    except requests.RequestException:
        return None

    if len(walcl_rows) < 2 or len(tga_rows) < 2 or not rrp_rows:
        return None

    walcl_date, walcl_latest = walcl_rows[-1]
    _, walcl_prev = walcl_rows[-2]
    tga_date, tga_latest = tga_rows[-1]
    _, tga_prev = tga_rows[-2]
    rrp_date, rrp_latest = rrp_rows[-1]

    # 單位統一換算成十億美元(WALCL/TGA原始為百萬美元,RRP本身就是十億美元)
    net_liq = walcl_latest / 1000 - tga_latest / 1000 - rrp_latest
    net_liq_prev = walcl_prev / 1000 - tga_prev / 1000 - rrp_latest
    net_liq_change = net_liq - net_liq_prev

    walcl_change = walcl_latest - walcl_prev
    tga_change = tga_latest - tga_prev

    # 規則式結論(不用AI)：TGA上升=資金被抽離系統(緊縮)，TGA下降=資金
    # 釋出(寬鬆)；WALCL攀升=擴表(寬鬆)，WALCL下滑=縮表(緊縮)。
    drivers = []
    if walcl_change > 0:
        drivers.append("WALCL擴張")
    elif walcl_change < 0:
        drivers.append("WALCL收縮")
    if tga_change > 0:
        drivers.append("TGA累積(抽離流動性)")
    elif tga_change < 0:
        drivers.append("TGA消耗(釋出流動性)")
    driver_text = "、".join(drivers) if drivers else "WALCL/TGA本週無明顯變化"

    if net_liq_change > 0:
        direction, verb = "寬鬆", "增加"
    elif net_liq_change < 0:
        direction, verb = "緊縮", "減少"
    else:
        direction, verb = "持平", "持平"

    conclusion = (
        f"淨流動性本週{verb}約{abs(net_liq_change):.0f}十億美元，"
        f"主因{driver_text}，環境偏{direction}。"
    )

    return {
        "walcl": walcl_latest, "walcl_change_pct": (walcl_change / walcl_prev * 100) if walcl_prev else 0,
        "walcl_date": walcl_date,
        "tga": tga_latest, "tga_change_pct": (tga_change / tga_prev * 100) if tga_prev else 0,
        "tga_date": tga_date,
        "rrp": rrp_latest, "rrp_date": rrp_date,
        "net_liquidity": net_liq,
        "net_liquidity_change_pct": (net_liq_change / net_liq_prev * 100) if net_liq_prev else 0,
        "conclusion": conclusion,
    }


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
        alert = _check_alert(key, latest, change, rows)
        any_alert = any_alert or alert

        arrow = "🔺" if change > 0 else ("🔻" if change < 0 else "▪")
        flag = " ⚠️" if alert else ""
        lines.append(
            f"- {label}：{latest}{unit} "
            f"{arrow}{change:+.2f}{unit}（{latest_date}）{flag}"
        )

    if not any_success:
        return []

    liquidity = _fetch_liquidity()
    if liquidity:
        lines.append("")
        lines.append(
            f"- Fed資產負債表(WALCL)：{liquidity['walcl'] / 1000:.0f}十億美元 "
            f"{'🔺' if liquidity['walcl_change_pct'] > 0 else '🔻' if liquidity['walcl_change_pct'] < 0 else '▪'}"
            f"{liquidity['walcl_change_pct']:+.2f}%（{liquidity['walcl_date']}，週頻資料）"
        )
        lines.append(
            f"- 財政部一般帳戶(TGA)：{liquidity['tga'] / 1000:.0f}十億美元 "
            f"{'🔺' if liquidity['tga_change_pct'] > 0 else '🔻' if liquidity['tga_change_pct'] < 0 else '▪'}"
            f"{liquidity['tga_change_pct']:+.1f}%（{liquidity['tga_date']}，週頻資料）"
        )
        lines.append(
            f"- 隔夜逆回購(RRP)：{liquidity['rrp']:.2f}十億美元（{liquidity['rrp_date']}）"
        )
        lines.append(
            f"- **淨流動性(WALCL-TGA-RRP)：{liquidity['net_liquidity']:.0f}十億美元 "
            f"{'🔺' if liquidity['net_liquidity_change_pct'] > 0 else '🔻' if liquidity['net_liquidity_change_pct'] < 0 else '▪'}"
            f"{liquidity['net_liquidity_change_pct']:+.1f}%(週變動)**"
        )
        lines.append(f"- 📝 {liquidity['conclusion']}")

    report = f"= 總經指標追蹤（{today}）=\n\n" + "\n".join(lines)

    item = {
        "title": f"總經指標追蹤（{today}）",
        "summary": report,
        "url": f"{BASE_URL}/series/{SERIES['vix'][0]}",
        "published_at": today,
        "telegram_alert": any_alert,
    }
    if any_alert:
        item["secondary_webhook_env"] = ALERT_WEBHOOK_ENV
    return [item]


if __name__ == "__main__":
    import sys
    result = fetch()
    if result:
        sys.stdout.reconfigure(encoding="utf-8")
        print(result[0]["summary"])
        print("\ntelegram_alert:", result[0]["telegram_alert"])
    else:
        print("No data fetched")
