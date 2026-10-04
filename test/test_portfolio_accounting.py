"""
test/test_portfolio_accounting.py — 報酬計算、定期定額分批投入、強制平倉記帳、回測引擎（2026-10-04）。

不打網路：回測用合成 K 線，db 指向暫存檔。
跑法：python -m unittest test.test_portfolio_accounting -v
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import backtest
import db
import index_dca_engine
import portfolio_metrics as pm
import price_feed


class MetricsTest(unittest.TestCase):
    def test_return_excludes_deposits(self):
        # 起始 1000 + 入金 200，市值 1200：沒有賺錢，舊版會顯示 +20%
        self.assertEqual(pm.return_excluding_deposits(1200, 1000, 200), 0.0)

    def test_xirr_one_year_ten_percent(self):
        r = pm.xirr([(date(2025, 1, 1), -100), (date(2026, 1, 1), 110)])
        self.assertAlmostEqual(r, 0.10, places=4)
        self.assertIsNone(pm.xirr([(date(2025, 1, 1), -100)]))

    def test_max_drawdown(self):
        self.assertAlmostEqual(pm.max_drawdown([100, 120, 90, 130, 65]), 0.5)


class DcaTrancheTest(unittest.TestCase):
    def test_idle_capital_goes_in_one_tranche_per_month(self):
        plans = index_dca_engine.decide_monthly_buys(1100.0, {})  # 起始 1000 + 當月入金 100
        self.assertAlmostEqual(sum(p["amount"] for p in plans), 200.0)
        plans = index_dca_engine.decide_monthly_buys(100.0, {})  # 起始本金用完後只投當月入金
        self.assertAlmostEqual(sum(p["amount"] for p in plans), 100.0)


class LiquidationBookkeepingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(tmp.name) / "t.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        db.init_portfolios()

    def test_liquidation_loses_margin_only_and_costs_reduce_pnl(self):
        pid = "crypto_futures"
        p1 = db.open_position(pid, "BTC", "long", 1.0, 100.0, 20.0, "2026-10-04", "t")  # 保證金 5
        self.assertAlmostEqual(db.get_portfolio(pid)["current_cash"], 95.0)
        pnl = db.close_position(p1, 80.0, "2026-10-04", "t", liquidated=True)  # 價格虧損 20 > 保證金 5
        self.assertAlmostEqual(pnl, -5.0)
        self.assertAlmostEqual(db.get_portfolio(pid)["current_cash"], 95.0)  # 不會倒扣成負數
        p2 = db.open_position(pid, "ETH", "long", 1.0, 10.0, 1.0, "2026-10-04", "t")
        self.assertAlmostEqual(db.close_position(p2, 12.0, "2026-10-04", "t", costs=0.5), 1.5)
        self.assertEqual([t["action"] for t in db.get_trades_on(pid, "2026-10-04")],
                         ["open_long", "close", "open_long", "close"])

    def test_deposits_are_listed(self):
        db.deposit_cash("tw_stock", 100.0, "2026-10-01", "t")
        self.assertEqual(db.get_deposits("tw_stock"), [{"trade_date": "2026-10-01", "amount": 100.0}])


def _bars(closes):
    return [{"open_ms": i, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c} for i, c in enumerate(closes)]


class BacktestEngineTest(unittest.TestCase):
    def setUp(self):
        # 緩跌→連漲幾天（出現向上交叉、進場）→剛進場就連續每天 −10%，再慢慢回升
        down = [100 - i * 0.2 for i in range(backtest.WINDOW)]
        up = [down[-1] + 2 * (i + 1) for i in range(6)]
        crash = [up[-1] * 0.9 ** (i + 1) for i in range(4)]
        recover = [crash[-1] + i for i in range(40)]
        closes = down + up + crash + recover
        self.bars = {s: _bars(closes) for s in backtest.SYMBOLS}
        self.snaps = {s: backtest.snapshots(v) for s, v in self.bars.items()}

    def test_runs_and_reports_every_metric(self):
        r = backtest.simulate("crypto_discretionary", {"strategy": "sma_cross", "leverage": 1.0,
                                                       "stop_pct": 0.05, "take_profit_pct": None}, self.bars, self.snaps)
        for key in ("final", "cagr", "max_dd", "trades", "win_rate", "liquidations", "exposure"):
            self.assertIn(key, r)
        self.assertGreater(r["trades"], 0)
        self.assertEqual(r["liquidations"], 0)  # 1 倍槓桿不會被強制平倉

    def test_high_leverage_gets_liquidated(self):
        r = backtest.simulate("crypto_futures", {"strategy": "sma_cross", "leverage": 20.0,
                                                 "stop_pct": None, "take_profit_pct": None}, self.bars, self.snaps)
        self.assertGreater(r["liquidations"], 0)

    def test_snapshots_match_live_computation(self):
        closes = [b["close"] for b in self.bars["BTC"]]
        t = backtest.WINDOW + 10
        self.assertEqual(self.snaps["BTC"][t], price_feed.compute_snapshot(closes[t - backtest.WINDOW + 1:t + 1]))

    def test_pick_skips_liquidated_and_losing_rows(self):
        rows = [
            {"strategy": "sma_cross", "leverage": 20.0, "take_profit_pct": None, "liquidations": 3, "final": 500, "max_dd": 0.01},
            {"strategy": "bollinger", "leverage": 1.0, "take_profit_pct": None, "liquidations": 0, "final": 90, "max_dd": 0.02},
            {"strategy": "macd_cross", "leverage": 3.0, "take_profit_pct": None, "liquidations": 0, "final": 200, "max_dd": 0.10},
            {"strategy": "sma_cross", "leverage": 1.0, "take_profit_pct": 0.1, "liquidations": 0, "final": 150, "max_dd": 0.11},
        ]
        # 回撤差距 2 個百分點內取槓桿低的
        self.assertEqual(backtest.pick(rows)["leverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
