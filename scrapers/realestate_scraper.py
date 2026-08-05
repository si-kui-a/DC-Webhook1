"""
不動產實價登錄批次資料下載(台中/台南/高雄)——2026-08-05從finfeed併入。
資料源: 內政部不動產成交案件實際資訊資料供應系統(全國批次資料,按季下載)。

搬移原因：finfeed（獨立GitHub Actions雲端專案，本機零足跡）的獎學金/
模擬投資邏輯已被ip自己更成熟的版本取代（scholarship_*.py／rule_engine.py），
但這支房地產首購快報是ip完全沒有的能力，2026-08-05使用者確認要完整搬過來，
不是二選一保留精簡版。逐字搬移，只調整成ip自己的模組匯入慣例。
"""
from curl_cffi import requests as curl_requests
import zipfile
import io
import csv
from collections import defaultdict

SEASON_URL_TEMPLATE = "http://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip"

CITY_CODES = {
    "b": "台中市",
    "d": "台南市",
    "e": "高雄市",
}


def roc_year_seasons(count: int = 4) -> list:
    """產生最近N季的季別代碼,例如 ['114S1','113S4','113S3','113S2']"""
    import datetime
    now = datetime.datetime.now()
    roc_year = now.year - 1911
    current_season = (now.month - 1) // 3 + 1

    seasons = []
    y, s = roc_year, current_season
    for _ in range(count):
        s -= 1
        if s == 0:
            s = 4
            y -= 1
        seasons.append(f"{y}S{s}")
    return seasons


def download_season_zip(season: str) -> bytes:
    """下載指定季別的全國實價登錄批次資料"""
    url = SEASON_URL_TEMPLATE.format(season=season)
    resp = curl_requests.get(url, impersonate="chrome", timeout=60)
    resp.raise_for_status()
    return resp.content


def extract_city_rows(zip_bytes: bytes, city_code: str) -> list:
    """從ZIP中取出指定縣市買賣案件的真實資料列(跳過表頭與BOM)"""
    filename = f"{city_code}_lvr_land_a.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            if filename not in z.namelist():
                return []
            with z.open(filename) as f:
                text = f.read().decode("utf-8-sig", errors="ignore")
    except zipfile.BadZipFile:
        return []

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 3:
        return []

    header = rows[0]
    data_rows = rows[2:]

    parsed = []
    for row in data_rows:
        if len(row) != len(header):
            continue
        record = dict(zip(header, row))
        parsed.append(record)
    return parsed


if __name__ == "__main__":
    seasons = roc_year_seasons(count=4)
    print(f"[1/3] 準備抓取最近 {len(seasons)} 季資料: {seasons}\n")

    city_season_data = defaultdict(lambda: defaultdict(list))

    for season in seasons:
        print(f"[2/3] 下載 {season} 季資料...")
        try:
            zip_bytes = download_season_zip(season)
        except Exception as e:
            print(f"  ⚠ {season} 下載失敗: {e}")
            continue

        for code, city_name in CITY_CODES.items():
            rows = extract_city_rows(zip_bytes, code)
            for r in rows:
                unit_price_str = r.get("單價元平方公尺", "").strip()
                if not unit_price_str:
                    continue
                try:
                    price = float(unit_price_str)
                    if price <= 0:
                        continue
                except ValueError:
                    continue
                city_season_data[city_name][season].append(price)

    print("\n[3/3] 縣市總結摘要(依季別排序,萬元/坪):\n")

    for city_name in CITY_CODES.values():
        print(f"=== {city_name} ===")
        season_stats = city_season_data[city_name]
        sorted_seasons = sorted(season_stats.keys())

        prev_avg = None
        for s in sorted_seasons:
            prices = season_stats[s]
            if not prices:
                continue
            avg_price = sum(prices) / len(prices)
            avg_per_ping = round(avg_price * 3.30579 / 10000, 2)
            total_count = len(prices)

            change = ""
            if prev_avg is not None and prev_avg > 0:
                pct = (avg_per_ping - prev_avg) / prev_avg * 100
                arrow = "↑" if pct > 0 else ("↓" if pct < 0 else "→")
                change = f" ({arrow}{pct:+.1f}%)"

            print(f"  {s}: 全市均價 {avg_per_ping} 萬/坪 | "
                  f"總成交 {total_count} 筆{change}")
            prev_avg = avg_per_ping

        if len(sorted_seasons) >= 2:
            first_price = round(
                sum(season_stats[sorted_seasons[0]]) / len(season_stats[sorted_seasons[0]]) * 3.30579 / 10000, 2
            )
            last_price = prev_avg
            if first_price > 0:
                total_change = (last_price - first_price) / first_price * 100
                trend_word = "上漲" if total_change > 0 else ("下跌" if total_change < 0 else "持平")
                print(f"  → 近{len(sorted_seasons)}季總趨勢: {trend_word} {abs(total_change):.1f}%")
        print()
