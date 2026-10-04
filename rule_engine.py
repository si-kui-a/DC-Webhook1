"""
rule_engine.py — 加密貨幣模擬持倉的規則式決策引擎（零 AI）。

live（jobs/portfolio.py）與回測（backtest.py）共用這裡的同一組函式：進出場訊號、
停損、強制平倉、交易成本。回測驗證的就是實際在跑的邏輯，不會各寫一份而長歪。

2026-10-04 改版（使用者核准的整併修法，見當日 PR）：
- 進場改成「真的交叉」（前一根 K 線在下、這一根在上）。舊版只看「SMA5 > SMA20」這個狀態，
  停損或停利後下一輪又立刻開同一筆倉（09-26 BTC 切損 20 分鐘後在幾乎同價重新進場）。
- 固定比例停損取代 AI 安全閥：交易決策不再呼叫 Gemini，額度用完也不會失去停損。
- 模擬強制平倉、手續費、資金費率：舊版虧損可以超過保證金、現金可以變負數。
- 移除價格觸發機制：規則不花額度，每輪直接評估。
策略與參數由 backtest.py 依使用者核准的規則選出（排除曾被強制平倉的組合，取最大
回撤最低者；差距 2 個百分點內取槓桿低、規則少的），結果附在當日 PR。

技術指標只用已收盤的日線（price_feed.compute_snapshot）。
"""
from datetime import datetime, timezone

WATCHLISTS = {
    "crypto_futures": ["BTC", "ETH"],
    "crypto_discretionary": ["BTC", "ETH"],
}

# 各帳戶的策略參數（backtest.py 2026-10-04 依核准規則選定，2017-10～2026-10 日線：
# 兩帳戶皆 最大回撤 ~9.4%、年化 ~5%、0 次強制平倉；同期 BTC/ETH 各半持有 年化 31% 但回撤 88%）。
# 改這裡前先重跑 backtest.py。
PARAMS = {
    "crypto_futures": {"strategy": "sma_cross", "leverage": 1.0, "stop_pct": 0.05, "take_profit_pct": 0.10},
    "crypto_discretionary": {"strategy": "sma_cross", "leverage": 1.0, "stop_pct": 0.05, "take_profit_pct": 0.10},
}
ENTRY_CASH_RATIO = 0.2

# 交易成本（Binance 公開費率的近似值，非 VIP、未用 BNB 折抵）
FEE_RATE = {"crypto_futures": 0.0005, "crypto_discretionary": 0.001}  # 每邊、名目金額的比例
FUNDING_RATE_DAILY = {"crypto_futures": 0.0003, "crypto_discretionary": 0.0}  # 多單長期平均約 0.01%/8h
MAINTENANCE_MARGIN_RATE = 0.005  # 小額部位第一級維持保證金率（BTC 0.4%、ETH 0.5%，取較嚴者）

STRATEGIES = ("sma_state", "sma_cross", "macd_cross", "bollinger")


def entry_signal(strategy: str, snap: dict | None) -> bool:
    """收盤後該不該進場。snap 是 price_feed.compute_snapshot() 的結果；欄位不足一律不進場。"""
    if not snap:
        return False
    try:
        if strategy == "sma_state":  # 舊版寫法，只留給回測對照
            return snap["sma5"] > snap["sma20"]
        if strategy == "sma_cross":
            return snap["prev_sma5"] <= snap["prev_sma20"] and snap["sma5"] > snap["sma20"]
        if strategy == "macd_cross":
            return snap["prev_macd"] <= snap["prev_macd_signal"] and snap["macd"] > snap["macd_signal"]
        if strategy == "bollinger":  # 均值回歸：收盤跌破下軌買進
            return snap["latest"] < snap["bollinger_lower"]
    except KeyError:
        return False
    raise ValueError(f"unknown strategy {strategy!r}")


def exit_signal(strategy: str, snap: dict | None) -> bool:
    """收盤後持倉該不該依訊號出場（停損與強制平倉另外判斷）。"""
    if not snap:
        return False
    try:
        if strategy in ("sma_state", "sma_cross"):
            return snap["sma5"] < snap["sma20"]
        if strategy == "macd_cross":
            return snap["macd"] < snap["macd_signal"]
        if strategy == "bollinger":  # 回到中軌就出場
            return snap["latest"] > snap["bollinger_middle"]
    except KeyError:
        return False
    raise ValueError(f"unknown strategy {strategy!r}")


def margin_used(position: dict) -> float:
    return position["avg_cost"] * position["quantity"] / position["leverage"]


def liquidation_price(position: dict) -> float | None:
    """多單權益（保證金＋未實現損益）跌到維持保證金時的價格；1 倍槓桿沒有強制平倉。
    推導：c·q/L + (p−c)·q = p·q·MMR  →  p = c·(1 − 1/L) / (1 − MMR)"""
    lev = position["leverage"]
    if lev <= 1:
        return None
    return position["avg_cost"] * (1 - 1 / lev) / (1 - MAINTENANCE_MARGIN_RATE)


def trade_costs(portfolio_id: str, position: dict, exit_price: float, held_days: float) -> float:
    """一進一出的手續費加上持有期間的資金費率，平倉時一次扣除。"""
    fee = FEE_RATE.get(portfolio_id, 0.0)
    entry_notional = position["avg_cost"] * position["quantity"]
    exit_notional = exit_price * position["quantity"]
    funding = FUNDING_RATE_DAILY.get(portfolio_id, 0.0) * entry_notional * max(held_days, 0.0)
    return fee * (entry_notional + exit_notional) + funding


def held_days(position: dict, now: datetime | None = None) -> float:
    opened = datetime.fromisoformat(position["opened_at"])
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - opened).total_seconds() / 86400


def decide_exit(portfolio_id: str, position: dict, price: float, snap: dict | None,
                params: dict | None = None) -> tuple[str, str] | None:
    """持倉在 price 時要不要出場：回傳 (kind, 理由)，kind 是 liquidation／stop／take_profit／signal；
    不出場回傳 None。snap 為 None 時只檢查價格類條件（回測用它檢查 K 線盤中低點）。
    params 預設用 PARAMS[portfolio_id]；回測傳入候選參數。"""
    p = params or PARAMS[portfolio_id]
    cost = position["avg_cost"]
    liq = liquidation_price(position)
    if liq is not None and price <= liq:
        return "liquidation", f"價格{price:g}跌破強制平倉價{liq:g}（{position['leverage']:g}倍槓桿）"
    stop = p["stop_pct"]
    if stop is not None and price <= cost * (1 - stop):
        return "stop", f"價格{price:g}跌破進場價{cost:g}的−{stop:.0%}，停損出場"
    tp = p["take_profit_pct"]
    if tp is not None and price >= cost * (1 + tp):
        return "take_profit", f"價格{price:g}達進場價{cost:g}的+{tp:.0%}，停利出場"
    if exit_signal(p["strategy"], snap):
        return "signal", f"{p['strategy']} 出場訊號（收盤後判斷）"
    return None


def build_trade_decision(portfolio: dict, positions: list[dict], snapshots: dict[str, dict | None],
                         traded_this_bar: set[str] | frozenset = frozenset()) -> list[dict]:
    """一輪決策：先處理每筆持倉的出場，再對空手的觀察標的判斷進場。
    positions 需已附 current_price；snapshots 是 {symbol: compute_snapshot 結果}。
    traded_this_bar：最近一根日線收盤後已經交易過的標的。live 每小時跑一次，但日線訊號
    一天才更新一次——當天停損出場後，下一小時看到的仍是同一根 K 線的交叉，會再開一次倉；
    同一根訊號 K 線只進場一次，跟回測的「一天一次決策」一致。出場不受限制。
    回傳動作清單（open／close），沒事就是空清單。"""
    portfolio_id = portfolio["portfolio_id"]
    p = PARAMS.get(portfolio_id)
    if p is None:
        return []
    actions = []
    held = set()
    for pos in positions:
        held.add(pos["symbol"])
        exit_ = decide_exit(portfolio_id, pos, pos["current_price"], snapshots.get(pos["symbol"]))
        if exit_:
            kind, reason = exit_
            actions.append({"action": "close", "kind": kind, "symbol": pos["symbol"],
                            "position_id": pos["position_id"], "reasoning": reason})
    for symbol in WATCHLISTS.get(portfolio_id, []):
        if symbol in held or symbol in traded_this_bar:
            continue
        if entry_signal(p["strategy"], snapshots.get(symbol)):
            actions.append({"action": "open", "symbol": symbol, "side": "long",
                            "cash_ratio": ENTRY_CASH_RATIO, "leverage": p["leverage"],
                            "reasoning": f"{p['strategy']} 進場訊號（收盤後判斷）"})
    return actions
