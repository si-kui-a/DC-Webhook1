"""backtest.py — 用 Binance 歷史日線重播 rule_engine，比較策略、槓桿、停損，依核准規則選參數。

跟 live 用同一套程式：指標是 price_feed.compute_snapshot()（同樣 60 根已收盤日線），出場是
rule_engine.decide_exit()、進場是 rule_engine.entry_signal()、成本是 rule_engine.trade_costs()。
訊號在第 t 天收盤後判斷、第 t+1 天開盤成交（不偷看當天價格）；停損與強制平倉用第 t+1 天的
最低價檢查。同一輪剛出場的標的當輪不再進場（跟 live 的 build_trade_decision 一致）。

選參數規則（使用者 2026-10-04 核准）：排除曾被強制平倉的組合，取最大回撤最低者；差距 2 個
百分點內取槓桿低、規則少的。另加一條：總報酬須為正（否則「不交易」更好，挑回撤最低會挑到
幾乎不進場的組合）。舊版寫法 sma_state 只當對照，不參與挑選。

Usage:
  venv/Scripts/python.exe backtest.py [--start 2017-09-01] [--json OUT.json]
Exit 0；網路取不到資料時 exit 2。
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import portfolio_metrics as pm
import price_feed
import rule_engine as R

SYMBOLS = ["BTC", "ETH"]
START_CASH = 100.0
WINDOW = price_feed.CRYPTO_HISTORY_DAYS
STRATEGY_ORDER = ["sma_cross", "macd_cross", "bollinger"]  # 規則由少到多
GRID = {
    "crypto_futures": {"leverage": [1.0, 3.0, 20.0], "stop_pct": [0.05, 0.10], "take_profit_pct": [None, 0.10]},
    "crypto_discretionary": {"leverage": [1.0], "stop_pct": [0.05, 0.10], "take_profit_pct": [None, 0.10]},
}
# 2026-10-04 之前的實際規則：狀態式進場、20 倍、停利 = 保證金 10% = 價格 0.5%、沒有規則式停損
BASELINE = {"strategy": "sma_state", "leverage": 20.0, "stop_pct": None, "take_profit_pct": 0.005}


def fetch_bars(symbol: str, start: str) -> list[dict]:
    ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    out: list[dict] = []
    while True:
        page = price_feed.get_crypto_daily_bars(symbol, start_ms=ms, limit=1000)
        if not page:
            break
        out += page
        if len(page) < 999:
            break
        ms = page[-1]["open_ms"] + 1
    return out


def align(bars: dict[str, list[dict]]) -> dict[str, list[dict]]:
    common = set.intersection(*({b["open_ms"] for b in v} for v in bars.values()))
    return {s: [b for b in v if b["open_ms"] in common] for s, v in bars.items()}


def snapshots(bars: list[dict]) -> list[dict | None]:
    closes = [b["close"] for b in bars]
    return [price_feed.compute_snapshot(closes[t - WINDOW + 1:t + 1]) if t >= WINDOW - 1 else None
            for t in range(len(bars))]


def simulate(pid: str, params: dict, bars: dict[str, list[dict]], snaps: dict[str, list]) -> dict:
    n = len(bars[SYMBOLS[0]])
    cash, pos, equity, pnls, liqs, exposed = START_CASH, {}, [], [], 0, 0
    for d in range(WINDOW, n):
        t = d - 1
        held_at_start = set(pos)
        for sym in list(pos):
            p, bar = pos[sym], bars[sym][d]
            ex = R.decide_exit(pid, p, bar["open"], snaps[sym][t], params)
            price = bar["open"]
            if not ex:
                ex = R.decide_exit(pid, p, bar["low"], None, params)
                if ex and ex[0] == "stop":
                    price = p["avg_cost"] * (1 - params["stop_pct"])
            if not ex:
                continue
            margin = R.margin_used(p)
            if ex[0] == "liquidation":
                pnl, liqs = -margin, liqs + 1
            else:
                pnl = (price - p["avg_cost"]) * p["quantity"] - R.trade_costs(pid, p, price, d - p["day"])
            cash += margin + pnl
            pnls.append(pnl)
            del pos[sym]
        for sym in SYMBOLS:
            if sym in held_at_start or sym in pos:
                continue
            if R.entry_signal(params["strategy"], snaps[sym][t]):
                margin = cash * R.ENTRY_CASH_RATIO
                if margin <= 0:
                    continue
                price = bars[sym][d]["open"]
                pos[sym] = {"avg_cost": price, "quantity": margin * params["leverage"] / price,
                            "leverage": params["leverage"], "day": d}
                cash -= margin
        exposed += bool(pos)
        equity.append(cash + sum(max(0.0, R.margin_used(p) + (bars[s][d]["close"] - p["avg_cost"]) * p["quantity"])
                                 for s, p in pos.items()))
    days = n - WINDOW
    half = len(equity) // 2
    return {
        "final": equity[-1], "cagr": pm.cagr(START_CASH, equity[-1], days),
        "cagr_1st_half": pm.cagr(START_CASH, equity[half - 1], half),
        "cagr_2nd_half": pm.cagr(equity[half - 1], equity[-1], len(equity) - half),
        "max_dd": pm.max_drawdown(equity), "trades": len(pnls),
        "win_rate": (sum(x > 0 for x in pnls) / len(pnls)) if pnls else 0.0,
        "liquidations": liqs, "exposure": exposed / days,
    }


def buy_and_hold(bars: dict[str, list[dict]]) -> dict:
    n = len(bars[SYMBOLS[0]])
    shares = {s: START_CASH / len(SYMBOLS) / bars[s][WINDOW]["open"] for s in SYMBOLS}
    equity = [sum(shares[s] * bars[s][d]["close"] for s in SYMBOLS) for d in range(WINDOW, n)]
    return {"final": equity[-1], "cagr": pm.cagr(START_CASH, equity[-1], n - WINDOW), "max_dd": pm.max_drawdown(equity)}


def candidates(pid: str):
    g = GRID[pid]
    for strategy in STRATEGY_ORDER:
        for lev in g["leverage"]:
            for stop in g["stop_pct"]:
                for tp in g["take_profit_pct"]:
                    yield {"strategy": strategy, "leverage": lev, "stop_pct": stop, "take_profit_pct": tp}


def pick(rows: list[dict]) -> dict | None:
    ok = [r for r in rows if r["strategy"] != "sma_state" and r["liquidations"] == 0 and r["final"] > START_CASH]
    if not ok:
        return None
    best = min(r["max_dd"] for r in ok)
    pool = [r for r in ok if r["max_dd"] <= best + 0.02]
    return min(pool, key=lambda r: (r["leverage"], r["take_profit_pct"] is not None,
                                    STRATEGY_ORDER.index(r["strategy"]), r["max_dd"]))


def fmt(r: dict) -> str:
    tp = "-" if r.get("take_profit_pct") is None else f"{r['take_profit_pct']:.1%}"
    stop = "-" if r.get("stop_pct") is None else f"{r['stop_pct']:.0%}"
    return (f"{r['strategy']:<10} {r['leverage']:>4g}x stop {stop:>4} tp {tp:>5} | final {r['final']:>9.1f}"
            f" CAGR {r['cagr']:>7.1%} (H1 {r['cagr_1st_half']:>7.1%} H2 {r['cagr_2nd_half']:>7.1%})"
            f" maxDD {r['max_dd']:>6.1%} trades {r['trades']:>4} win {r['win_rate']:>4.0%}"
            f" liq {r['liquidations']:>3} exp {r['exposure']:>4.0%}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--start", default="2017-09-01")
    ap.add_argument("--json", help="write every row and the picks to this file")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    raw = {s: fetch_bars(s, args.start) for s in SYMBOLS}
    if not all(raw.values()):
        print("no data from Binance")
        return 2
    bars = align(raw)
    snaps = {s: snapshots(v) for s, v in bars.items()}
    first = datetime.fromtimestamp(bars[SYMBOLS[0]][WINDOW]["open_ms"] / 1000, timezone.utc).date()
    last = datetime.fromtimestamp(bars[SYMBOLS[0]][-1]["open_ms"] / 1000, timezone.utc).date()
    bh = buy_and_hold(bars)
    print(f"{first} ~ {last}, {len(bars[SYMBOLS[0]]) - WINDOW} days; buy & hold 50/50 BTC/ETH:"
          f" final {bh['final']:.1f} CAGR {bh['cagr']:.1%} maxDD {bh['max_dd']:.1%}")
    report = {"period": [str(first), str(last)], "buy_and_hold": bh, "accounts": {}}
    for pid in GRID:
        rows = [{**c, **simulate(pid, c, bars, snaps)} for c in candidates(pid)]
        base = {**BASELINE, **simulate(pid, BASELINE, bars, snaps)} if pid == "crypto_futures" else None
        chosen = pick(rows)
        print(f"\n== {pid}")
        if base:
            print("  old rules  " + fmt(base))
        for r in sorted(rows, key=lambda r: r["max_dd"]):
            print(("  PICK " if r is chosen else "       ") + fmt(r))
        report["accounts"][pid] = {"rows": rows, "baseline": base, "pick": chosen}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
