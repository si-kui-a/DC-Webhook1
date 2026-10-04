"""portfolio_metrics.py — 績效計算，live 推播與 backtest.py 共用。

- return_excluding_deposits：扣掉入金的累計報酬。舊版 (總資產−起始本金)/起始本金 把每月
  定期定額的入金也算成獲利（2026-10-04 review）。
- xirr：資金加權年化報酬，定期定額帳戶看這個——反映每筆入金的時間點。
- max_drawdown／cagr：回測比較策略用（時間加權，不受入金時點影響）。
"""
from datetime import date


def return_excluding_deposits(total_value: float, starting_capital: float, deposits: float) -> float:
    invested = starting_capital + deposits
    return (total_value - invested) / invested if invested > 0 else 0.0


def max_drawdown(equity: list[float]) -> float:
    """最大回撤（0.25 = 從高點跌 25%）。"""
    peak, worst = 0.0, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def cagr(start_value: float, end_value: float, days: float) -> float:
    if start_value <= 0 or days <= 0:
        return 0.0
    if end_value <= 0:
        return -1.0
    return (end_value / start_value) ** (365 / days) - 1


def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """資金加權年化報酬。cashflows：投入為負、期末市值為正。二分法求解，解不出回傳 None。"""
    if len(cashflows) < 2 or not any(a < 0 for _, a in cashflows) or not any(a > 0 for _, a in cashflows):
        return None
    t0 = min(d for d, _ in cashflows)

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** ((d - t0).days / 365) for d, a in cashflows)

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
