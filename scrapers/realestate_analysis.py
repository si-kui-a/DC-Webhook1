"""
不動產區域首購評分(台中/台南/高雄)——2026-08-05從finfeed併入(見
realestate_scraper.py開頭的搬移原因說明)。
統計與評分為純程式邏輯(不呼叫AI),最後一步才交由AI做語意解讀
讀者是 20 多歲、預算有限的首購族:哪一區買得起且住得下、哪一區該避開、市場往哪走

⚠️TODO(2026-08-05)：finfeed原本的notifier/realestate_report.py會import
這支檔案的affordability()/BUYER/SUBSIDY（自備款/月收入/貸款成數試算出
「可負擔總價上限」），但finfeed當下的realestate_analysis.py其實從沒定義
過這三樣東西——7/27那次「改為20多歲首購族導向」的重構顯然沒做完就停在
這裡，portal端(finfeed)這個功能一直是壞的（ImportError）。這裡故意不
補這一層(不編造真實貸款利率/年限/自備款門檻等財務假設)，main.py的
run_realestate_report()目前只用得到本檔案這裡定義的評分/趨勢，「可負擔
總價上限」的財務試算層留待使用者提供真實的自備款/月收入/貸款成數/新青安
利率年限等數字後再補。
"""
from collections import Counter, defaultdict

from scrapers.realestate_scraper import (
    roc_year_seasons, download_season_zip, extract_city_rows, CITY_CODES
)

LOW_SAMPLE_THRESHOLD = 30
CRISIS_DECLINE_THRESHOLD = 15
VOLUME_CRASH_THRESHOLD = 30
VOLATILITY_THRESHOLD = 50
M2_PER_PING = 3.30579

# 單季成交低於此數的行政區「完全不顯示」,不進清單也不納入趨勢統計。
# 幾筆成交算出來的均價只是雜訊,對首購族而言看了比沒看更糟。
MIN_DISPLAY_SAMPLE = 30

# ─── 首購族設定(20 多歲、自備款有限、第一次買房) ──────────────
FIRST_HOME = {
    "總價上限": 10_000_000,  # 1000萬:中南部首購常見的貸款負擔天花板
    "房數": (2, 3),          # 2-3房。1房套房貸款成數低、轉手不易,不列入
    "坪數": (15, 40),        # 權狀坪數,太小難貸、太大買不起
}

# ─── 首購評分權重(滿分 10,四維度) ─────────────────────────
# 權重分配的理由寫在這裡,報告會原樣轉述給讀者,兩邊只有一份真相。
WEIGHTS = {
    "首購適配": (4, "這區有沒有你買得起、住得下的房子"),
    "價格趨勢": (3, "溫和增值最好,急漲代表你已經追不上"),
    "量能動能": (2, "有量才轉得手,首購通常 5-7 年會換屋"),
    "波動穩定": (1, "暴漲暴跌的價格不可靠"),
}
GRADE_ENTER = 8   # ≥ 此分 → 值得看
GRADE_AVOID = 4   # ≤ 此分 → 建議避開


def _to_num(value):
    """實價登錄欄位常有空字串或非數字備註,一律轉不動就當缺值"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def collect_district_data(seasons: list) -> tuple:
    """
    回傳 (價量資料, 房型結構):
      價量[行政區][季別] = {'均價': 萬元/坪, '筆數': n}
      房型[行政區]        = {'首購佔比','可入手總價中位','主力房型','建物筆數'}
    房型結構跨季合併統計 — 那是區域的供給體質,不是單季波動。
    """
    data = defaultdict(dict)
    # 跨季累計的房型結構原始資料
    raw = defaultdict(lambda: {"房數": Counter(), "建物筆數": 0, "可入手總價": []})

    lo_room, hi_room = FIRST_HOME["房數"]
    lo_ping, hi_ping = FIRST_HOME["坪數"]

    for season in seasons:
        print(f"下載 {season} 季資料...")
        try:
            zip_bytes = download_season_zip(season)
        except Exception as e:
            print(f"  ⚠ {season} 下載失敗: {e}")
            continue

        for code, city_name in CITY_CODES.items():
            rows = extract_city_rows(zip_bytes, code)
            district_prices = defaultdict(list)
            for r in rows:
                district = r.get("鄉鎮市區", "").strip()
                if not district:
                    continue
                price = _to_num(r.get("單價元平方公尺"))
                if price is None or price <= 0:
                    continue
                district_prices[district].append(price)

                # ── 房型/配置:只有含建物的成交才有格局與坪數 ──
                key = f"{city_name}{district}"
                rooms = _to_num(r.get("建物現況格局-房"))
                total = _to_num(r.get("總價元"))
                area = _to_num(r.get("建物移轉總面積平方公尺"))
                if not rooms or not total or not area or area <= 0:
                    continue
                rooms = int(rooms)
                ping = area / M2_PER_PING
                raw[key]["房數"][rooms] += 1
                raw[key]["建物筆數"] += 1
                if (lo_room <= rooms <= hi_room
                        and lo_ping <= ping <= hi_ping
                        and total <= FIRST_HOME["總價上限"]):
                    raw[key]["可入手總價"].append(total)

            for district, prices in district_prices.items():
                avg_per_ping = round(sum(prices) / len(prices) * M2_PER_PING / 10000, 2)
                key = f"{city_name}{district}"
                data[key][season] = {"均價": avg_per_ping, "筆數": len(prices)}

    profiles = _summarize_layouts(raw)
    if data and not profiles:
        # 欄位名稱若被改版,首購分數會整批歸零而不報錯 — 這裡出聲提醒
        print("  ⚠ 未解析到任何房型資料,請確認實價登錄欄位名稱是否變更")
    return data, profiles


def _summarize_layouts(raw: dict) -> dict:
    """把逐筆房型累計收斂成每區一組結構指標"""
    profiles = {}
    for key, acc in raw.items():
        if not acc["建物筆數"]:
            continue
        affordable = acc["可入手總價"]
        profiles[key] = {
            "建物筆數": acc["建物筆數"],
            "首購佔比": round(len(affordable) / acc["建物筆數"] * 100, 1),
            "可入手總價中位": round(_median(affordable) / 10000) if affordable else None,
            "主力房型": acc["房數"].most_common(1)[0][0],
        }
    return profiles


def analyze(data: dict) -> dict:
    """純程式統計篩選,回傳分類結果"""
    crisis = []
    promising = []
    volatile = []
    low_sample_flags = []

    for location, season_stats in data.items():
        sorted_seasons = sorted(season_stats.keys())
        if len(sorted_seasons) < 2:
            continue

        prices = [season_stats[s]["均價"] for s in sorted_seasons]
        counts = [season_stats[s]["筆數"] for s in sorted_seasons]

        if any(c < LOW_SAMPLE_THRESHOLD for c in counts):
            low_sample_flags.append({
                "地區": location,
                "最低單季筆數": min(counts),
            })

        total_change = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0

        if total_change <= -CRISIS_DECLINE_THRESHOLD:
            crisis.append({
                "地區": location,
                "總跌幅": round(total_change, 1),
                "最新均價": prices[-1],
                "最新筆數": counts[-1],
            })

        for i in range(1, len(counts)):
            if counts[i-1] > 0:
                vol_change = (counts[i] - counts[i-1]) / counts[i-1] * 100
                if vol_change <= -VOLUME_CRASH_THRESHOLD:
                    crisis.append({
                        "地區": location,
                        "警示": "成交量驟減",
                        "季別": sorted_seasons[i],
                        "量能變化": round(vol_change, 1),
                    })
                    break

        is_monotonic_up = all(prices[i] > prices[i-1] for i in range(1, len(prices)))
        volume_not_shrinking = counts[-1] >= counts[0] * 0.8
        if is_monotonic_up and volume_not_shrinking and total_change > 5:
            promising.append({
                "地區": location,
                "總漲幅": round(total_change, 1),
                "最新均價": prices[-1],
                "最新筆數": counts[-1],
            })

        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                pct = (prices[i] - prices[i-1]) / prices[i-1] * 100
                if abs(pct) >= VOLATILITY_THRESHOLD:
                    volatile.append({
                        "地區": location,
                        "季別": sorted_seasons[i],
                        "單季變化": round(pct, 1),
                        "該季筆數": counts[i],
                    })

    return {
        "危機/風險區": crisis,
        "前景看好區": promising,
        "異常波動區": volatile,
        "低樣本警示": low_sample_flags,
    }


def _score_one(location: str, season_stats: dict, profile: dict) -> dict | None:
    """
    單一行政區首購評分。回傳 None 代表不予顯示:
    季數不足無法比趨勢,或任一季成交低於 MIN_DISPLAY_SAMPLE。
    """
    seasons = sorted(season_stats)
    if len(seasons) < 2:
        return None

    prices = [season_stats[s]["均價"] for s in seasons]
    counts = [season_stats[s]["筆數"] for s in seasons]
    if prices[0] <= 0 or counts[0] <= 0:
        return None

    # 樣本門檻:直接不予顯示,不做「資料不足」分級也不進趨勢統計
    if min(counts) < MIN_DISPLAY_SAMPLE:
        return None

    price_change = (prices[-1] - prices[0]) / prices[0] * 100
    volume_change = (counts[-1] - counts[0]) / counts[0] * 100

    # 首購適配 (4):這區有多少比例的成交,是 2-3 房、15-40 坪、總價在預算內的
    share = profile.get("首購佔比", 0.0) if profile else 0.0
    if share >= 40:
        s_fit = 4
    elif share >= 25:
        s_fit = 3
    elif share >= 12:
        s_fit = 2
    elif share >= 5:
        s_fit = 1
    else:
        s_fit = 0

    # 價格趨勢 (3):首購要的是溫和增值。
    # 急漲對投資人是好消息,對首購族是「你已經追不上了」,因此高分給溫和段而非最猛段。
    if 3 <= price_change < 10:
        s_price = 3
    elif -3 <= price_change < 3:
        s_price = 2
    elif price_change >= 10:
        s_price = 1
    elif price_change > -CRISIS_DECLINE_THRESHOLD:
        s_price = 1
    else:
        s_price = 0

    # 量能動能 (2):首購通常 5-7 年換屋,沒量的區到時候賣不掉
    if volume_change >= 10:
        s_volume = 2
    elif volume_change > -VOLUME_CRASH_THRESHOLD:
        s_volume = 1
    else:
        s_volume = 0

    # 波動穩定 (1):單季暴衝過就扣光
    spikes = sum(
        1 for i in range(1, len(prices))
        if prices[i - 1] > 0 and abs((prices[i] - prices[i - 1]) / prices[i - 1] * 100) >= VOLATILITY_THRESHOLD
    )
    s_stable = 1 if spikes == 0 else 0

    score = s_fit + s_price + s_volume + s_stable
    if score >= GRADE_ENTER:
        grade = "推薦"
    elif score <= GRADE_AVOID:
        grade = "避開"
    else:
        grade = "觀望"

    # ── 判斷理由:給讀者看「為什麼是這個分數」,首購關心的擺第一 ──
    why = []
    rooms = profile.get("主力房型") if profile else None
    budget = profile.get("可入手總價中位") if profile else None
    if share >= 5 and budget:
        why.append(f"{rooms}房為主｜可入手總價中位 {budget:,.0f}萬")
    elif rooms:
        why.append(f"{rooms}房為主｜預算內物件稀少")
    else:
        why.append("無房型資料")

    if price_change >= 10:
        why.append(f"急漲 {price_change:+.1f}% 追價風險高")
    elif price_change <= -CRISIS_DECLINE_THRESHOLD:
        why.append(f"均價重挫 {price_change:+.1f}%")
    else:
        why.append(f"均價 {price_change:+.1f}%")

    if volume_change <= -VOLUME_CRASH_THRESHOLD:
        why.append(f"量能急凍 {volume_change:+.0f}% 難轉手")
    elif spikes:
        why.append(f"單季暴衝 {spikes} 次")

    return {
        "地區": location,
        "總分": score,
        "分級": grade,
        "均價": prices[-1],
        "價格變化": round(price_change, 1),
        "量能變化": round(volume_change, 1),
        "首購佔比": round(share, 1),
        "主力房型": rooms,
        "可入手總價中位": budget,
        "最低筆數": min(counts),
        "理由": "｜".join(why),
    }


def score_districts(data: dict, profiles: dict) -> tuple:
    """
    全區評分,依總分高到低排序(同分時首購佔比高的在前)。
    回傳 (清單, 未達樣本門檻而不予顯示的區數)。
    """
    scored = []
    for loc, stats in data.items():
        result = _score_one(loc, stats, profiles.get(loc, {}))
        if result:
            scored.append(result)
    scored.sort(key=lambda x: (-x["總分"], -x["首購佔比"]))
    return scored, len(data) - len(scored)


def _median(values: list) -> float:
    """中位數:實價登錄常有單筆豪宅拉高平均,用中位數看整體才不失真"""
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def market_trend(scored: list) -> dict:
    """整體市場趨勢:價量四象限判定 + 對首購族的意義(純規則,不經AI)"""
    if not scored:
        return {}

    price_med = _median([d["價格變化"] for d in scored])
    volume_med = _median([d["量能變化"] for d in scored])
    # 持平區(變化剛好 0)既不算漲也不算跌,否則會被歸進下跌而虛報跌勢
    up = sum(1 for d in scored if d["價格變化"] > 0)
    down = sum(1 for d in scored if d["價格變化"] < 0)
    affordable_med = _median([d["首購佔比"] for d in scored])

    if price_med >= 0 and volume_med >= 0:
        state, meaning = "價漲量增", "大家都在買,別急著追價,先確認自備款與貸款成數再出手"
    elif price_med >= 0 and volume_med < 0:
        state, meaning = "價漲量縮", "賣方在硬撐但成交變少,你有本錢慢慢看、慢慢殺價"
    elif price_med < 0 and volume_med < 0:
        state, meaning = "價跌量縮", "市場冷,議價空間大,但買了短期不好轉手,要打算長住"
    else:
        state, meaning = "價跌量增", "有人開始低接,通常是落底訊號,但還沒確立,可分批看屋"

    return {
        "區數": len(scored),
        "上漲區數": up,
        "下跌區數": down,
        "價格中位變化": round(price_med, 1),
        "量能中位變化": round(volume_med, 1),
        "首購佔比中位": round(affordable_med, 1),
        "價量狀態": state,
        "買方意義": meaning,
    }


def print_report(result: dict):
    print("\n" + "=" * 60)
    print("不動產區域風險/機會篩選報告")
    print("=" * 60)

    print(f"\n🔴 危機/風險區(共{len(result['危機/風險區'])}項)")
    print("-" * 40)
    if not result["危機/風險區"]:
        print("  未發現符合條件的區域")
    for item in result["危機/風險區"]:
        print(f"  {item}")

    print(f"\n🟢 前景看好區(共{len(result['前景看好區'])}項)")
    print("-" * 40)
    if not result["前景看好區"]:
        print("  未發現符合條件的區域")
    for item in result["前景看好區"]:
        print(f"  {item}")

    print(f"\n⚠️  異常波動區(單季變化超過{VOLATILITY_THRESHOLD}%,可能反映真實劇變或樣本失真)")
    print("-" * 40)
    if not result["異常波動區"]:
        print("  未發現符合條件的區域")
    for item in result["異常波動區"]:
        print(f"  {item}")

    print(f"\n⚪ 低樣本警示(單季成交低於{LOW_SAMPLE_THRESHOLD}筆,統計意義較弱,判讀請謹慎)")
    print("-" * 40)
    if not result["低樣本警示"]:
        print("  無")
    for item in result["低樣本警示"]:
        print(f"  {item}")

    print("\n" + "=" * 60)
    print("備註:本報告基於內政部實價登錄公開資料,純統計篩選產生,")
    print("      未經人工或AI語意判讀,實際決策仍需結合總體經濟指標與")
    print("      在地產業動態綜合評估。")
    print("=" * 60)


if __name__ == "__main__":
    # 開發用:只跑統計與評分,AI 解讀在 main.py 的 run_realestate_report()
    seasons = roc_year_seasons(count=4)
    print(f"分析區間: {seasons}\n")

    data, profiles = collect_district_data(seasons)
    print_report(analyze(data))

    scored, hidden = score_districts(data, profiles)
    print("\n" + "=" * 60)
    print(f"首購評分(滿分10,共{len(scored)}區,另{hidden}區樣本不足未顯示)")
    print("=" * 60)
    for d in scored:
        print(f"  {d['總分']:2d} [{d['分級']}] {d['地區']:<12} {d['均價']:>6.1f}萬/坪 "
              f"首購{d['首購佔比']:>5.1f}%  {d['理由']}")
    print(f"\n整體趨勢: {market_trend(scored)}")
