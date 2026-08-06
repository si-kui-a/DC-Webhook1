# -*- coding: utf-8 -*-
"""tw_stock模擬帳戶的規則式定期定額(DCA)決策引擎。

2026-08-06使用者指示：tw_stock帳戶原本每個交易日呼叫AI(Gemini)即時判斷
進出場，但自2026-07-30建立以來從未真正買進過(全部觀望)，且個股擇時判斷
不是這個帳戶的目的。改為以指數型ETF(0050/006208)定期定額為主軸，純規則
計算、完全不呼叫AI——符合CLAUDE.md「低AI依賴，結構化資料一律用純程式
處理」原則，也讓帳戶真正開始建倉而非持續觀望。

策略：
- 每月固定注入MONTHLY_CONTRIBUTION模擬現金(比照真實DCA需要持續新資金
  投入，而非只是把一次性本金分批進場)
- 按SPLIT_RATIO分配到0050/006208(同追蹤台灣50指數，分散於兩檔互為
  備援，不集中單一ETF)
- 依價格相對SMA20的乖離率調整買進倍數(乖離越負向=越便宜，買越多；
  乖離正向過大則減碼/暫停加碼)，即常見的「定期不定額/微笑曲線」做法，
  所有數字皆可由price_feed.get_technical_snapshot()客觀計算，不涉主觀判斷
"""

TARGET_SYMBOLS = ["0050", "006208"]
SPLIT_RATIO = {"0050": 0.5, "006208": 0.5}
MONTHLY_CONTRIBUTION = 100.0

# (乖離率上限, 加碼倍數)：由上而下比對，符合第一個滿足的門檻即採用。
# 乖離率 = (現價 - SMA20) / SMA20。
DEVIATION_TIERS = [
    (-0.10, 2.0),   # 跌破SMA20 10%以上 -> 2倍基準金額
    (-0.05, 1.5),   # 跌破SMA20 5%~10% -> 1.5倍
    (0.05, 1.0),    # -5%~+5%(正常區間) -> 1倍(基準金額)
    (0.10, 0.5),    # 高於SMA20 5%~10% -> 0.5倍
]
# 高於SMA20 10%以上 -> 0倍(暫停本月加碼，等回檔)


def _multiplier_for_deviation(deviation_pct: float) -> float:
    for threshold, multiplier in DEVIATION_TIERS:
        if deviation_pct <= threshold:
            return multiplier
    return 0.0


def decide_monthly_buys(available_cash: float, technical_snapshots: dict[str, dict]) -> list[dict]:
    """回傳每個TARGET_SYMBOLS的購買金額規劃。

    technical_snapshots: {symbol: price_feed.get_technical_snapshot()的回傳值}。
    金額總和超過available_cash時，等比例縮減全部規劃，確保不透支模擬帳戶
    現金(現金水位由呼叫端在deposit_cash()之後查詢，含本次注入的定額)。
    """
    plans = []
    for symbol in TARGET_SYMBOLS:
        symbol_base = MONTHLY_CONTRIBUTION * SPLIT_RATIO[symbol]
        snapshot = technical_snapshots.get(symbol)
        if not snapshot or "sma20" not in snapshot or not snapshot.get("latest"):
            plans.append({
                "symbol": symbol,
                "amount": symbol_base,
                "reasoning": "技術指標資料不足，採基準金額定期定額(不加減碼)",
            })
            continue

        deviation_pct = (snapshot["latest"] - snapshot["sma20"]) / snapshot["sma20"]
        multiplier = _multiplier_for_deviation(deviation_pct)
        amount = symbol_base * multiplier
        plans.append({
            "symbol": symbol,
            "amount": amount,
            "reasoning": f"價格相對SMA20乖離{deviation_pct:+.1%}，加碼倍數{multiplier:g}x"
                         f"（基準{symbol_base:.0f} → {amount:.0f}）",
        })

    total = sum(p["amount"] for p in plans)
    if total > available_cash and total > 0:
        scale = available_cash / total
        for p in plans:
            p["amount"] *= scale
            p["reasoning"] += f"；現金不足等比例縮減至{scale:.0%}"

    return plans
