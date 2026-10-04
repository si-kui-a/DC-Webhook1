"""
test/test_rule_engine.py — rule_engine.py（零 AI 規則決策）的單元測試。

2026-10-04 改版後重寫：真交叉進場、固定比例停損、停利、強制平倉、交易成本，以及
「同一輪剛出場的標的不再進場」。純 unittest，不打網路。

跑法：python -m unittest test.test_rule_engine -v
"""
import unittest
from datetime import datetime, timedelta, timezone

import rule_engine as R

PARAMS = {"strategy": "sma_cross", "leverage": 1.0, "stop_pct": 0.05, "take_profit_pct": 0.10}


def _portfolio(pid="crypto_discretionary"):
    return {"portfolio_id": pid, "current_cash": 100.0, "starting_capital": 100.0}


def _pos(symbol="BTC", avg_cost=100.0, price=100.0, leverage=1.0, quantity=1.0, position_id="p1"):
    return {"position_id": position_id, "symbol": symbol, "side": "long", "avg_cost": avg_cost,
            "quantity": quantity, "leverage": leverage, "current_price": price,
            "opened_at": datetime.now(timezone.utc).isoformat()}


def _snap(sma5, sma20, prev5, prev20, latest=100.0):
    return {"latest": latest, "sma5": sma5, "sma20": sma20, "prev_sma5": prev5, "prev_sma20": prev20}


CROSS_UP = _snap(101, 100, 99, 100)
ABOVE_NO_CROSS = _snap(105, 100, 104, 100)
CROSS_DOWN = _snap(99, 100, 101, 100)


class EntryTest(unittest.TestCase):
    def test_enters_only_on_an_actual_cross(self):
        self.assertTrue(R.entry_signal("sma_cross", CROSS_UP))
        self.assertFalse(R.entry_signal("sma_cross", ABOVE_NO_CROSS))  # 舊版會進場的情況
        self.assertTrue(R.entry_signal("sma_state", ABOVE_NO_CROSS))

    def test_missing_fields_never_enter(self):
        self.assertFalse(R.entry_signal("sma_cross", {"latest": 1, "sma5": 2, "sma20": 1}))
        self.assertFalse(R.entry_signal("sma_cross", None))

    def test_open_action_uses_account_leverage_and_ratio(self):
        actions = R.build_trade_decision(_portfolio("crypto_futures"), [], {"BTC": CROSS_UP, "ETH": ABOVE_NO_CROSS})
        self.assertEqual([a["symbol"] for a in actions], ["BTC"])
        self.assertEqual(actions[0]["leverage"], R.PARAMS["crypto_futures"]["leverage"])
        self.assertEqual(actions[0]["cash_ratio"], R.ENTRY_CASH_RATIO)


class ExitTest(unittest.TestCase):
    def test_stop_take_profit_and_signal(self):
        self.assertEqual(R.decide_exit("x", _pos(), 94.9, None, PARAMS)[0], "stop")
        self.assertEqual(R.decide_exit("x", _pos(), 111.0, None, PARAMS)[0], "take_profit")
        self.assertEqual(R.decide_exit("x", _pos(), 100.0, CROSS_DOWN, PARAMS)[0], "signal")
        self.assertIsNone(R.decide_exit("x", _pos(), 100.0, ABOVE_NO_CROSS, PARAMS))

    def test_liquidation_comes_before_stop_at_high_leverage(self):
        pos = _pos(leverage=20.0)
        liq = R.liquidation_price(pos)
        self.assertAlmostEqual(liq, 100 * (1 - 1 / 20) / (1 - R.MAINTENANCE_MARGIN_RATE))
        self.assertEqual(R.decide_exit("x", pos, liq - 0.01, None, PARAMS)[0], "liquidation")
        self.assertIsNone(R.liquidation_price(_pos(leverage=1.0)))

    def test_closed_symbol_is_not_reopened_in_the_same_run(self):
        """09-26 BTC 切損 20 分鐘後又在同價開倉：出場那一輪即使訊號成立也不進場。"""
        actions = R.build_trade_decision(_portfolio(), [_pos(price=90.0)], {"BTC": CROSS_UP})
        self.assertEqual([(a["action"], a["symbol"]) for a in actions], [("close", "BTC")])

    def test_no_reentry_on_the_same_signal_bar_across_runs(self):
        """當天停損後，下一小時的同一根日線交叉不能再開倉；出場仍照常判斷。"""
        actions = R.build_trade_decision(_portfolio(), [], {"BTC": CROSS_UP, "ETH": CROSS_UP}, {"BTC"})
        self.assertEqual([a["symbol"] for a in actions], ["ETH"])
        actions = R.build_trade_decision(_portfolio(), [_pos(price=90.0)], {"BTC": CROSS_UP}, {"BTC"})
        self.assertEqual([a["action"] for a in actions], ["close"])

    def test_unknown_portfolio_returns_empty(self):
        self.assertEqual(R.build_trade_decision(_portfolio("nope"), [], {"BTC": CROSS_UP}), [])


class CostTest(unittest.TestCase):
    def test_fees_both_sides_plus_daily_funding(self):
        pos = _pos(avg_cost=100.0, quantity=2.0)
        got = R.trade_costs("crypto_futures", pos, 110.0, held_days=10)
        want = R.FEE_RATE["crypto_futures"] * (200 + 220) + R.FUNDING_RATE_DAILY["crypto_futures"] * 200 * 10
        self.assertAlmostEqual(got, want)
        self.assertAlmostEqual(R.trade_costs("crypto_discretionary", pos, 110.0, 10),
                               R.FEE_RATE["crypto_discretionary"] * 420)

    def test_held_days(self):
        pos = _pos()
        pos["opened_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.assertAlmostEqual(R.held_days(pos), 3, places=2)


if __name__ == "__main__":
    unittest.main()
