"""
test/test_rule_engine.py — rule_engine.py的規則式模擬持倉決策單元測試
(2026-08-04新增)。這個專案原本完全沒有自動測試，金融決策邏輯沒有測試
覆蓋率是真實風險，這次補上——純unittest+unittest.mock，不需要新依賴。

跑法：python -m unittest test.test_rule_engine -v
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import rule_engine


def _portfolio(portfolio_id: str) -> dict:
    return {"portfolio_id": portfolio_id, "current_cash": 100.0, "currency": "USDT", "starting_capital": 100.0}


def _position(symbol, side="long", quantity=1.0, avg_cost=100.0, leverage=1.0,
              current_price=100.0, unrealized_pnl=0.0, sma5=None, sma20=None,
              position_id="pos1", market_value=None):
    p = {
        "position_id": position_id, "symbol": symbol, "side": side,
        "quantity": quantity, "avg_cost": avg_cost, "leverage": leverage,
        "current_price": current_price, "unrealized_pnl": unrealized_pnl,
        "market_value": market_value if market_value is not None else avg_cost * quantity / leverage + unrealized_pnl,
    }
    if sma5 is not None:
        p["sma5"] = sma5
    if sma20 is not None:
        p["sma20"] = sma20
    return p


class TestNewEntry(unittest.TestCase):
    @patch("rule_engine.price_feed.get_technical_snapshot")
    def test_golden_cross_opens_position(self, mock_snapshot):
        mock_snapshot.return_value = {"latest": 105, "sma5": 105.0, "sma20": 100.0}
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [], [], [],
        )
        opens = [a for a in decision["actions"] if a["action"] == "open"]
        self.assertEqual(len(opens), 2)  # BTC + ETH都會觸發(同一個mock回傳值)
        for a in opens:
            self.assertEqual(a["cash_ratio"], rule_engine.ENTRY_CASH_RATIO)
            self.assertEqual(a["leverage"], 1.0)  # 非futures帳戶強制1.0
            self.assertEqual(a["side"], "long")

    @patch("rule_engine.price_feed.get_technical_snapshot")
    def test_golden_cross_futures_uses_configured_leverage(self, mock_snapshot):
        mock_snapshot.return_value = {"latest": 105, "sma5": 105.0, "sma20": 100.0}
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_futures"), [], [], [],
        )
        opens = [a for a in decision["actions"] if a["action"] == "open"]
        self.assertTrue(opens)
        for a in opens:
            self.assertEqual(a["leverage"], rule_engine.FUTURES_LEVERAGE)

    @patch("rule_engine.price_feed.get_technical_snapshot")
    def test_no_cross_yet_holds_and_sets_price_trigger(self, mock_snapshot):
        mock_snapshot.return_value = {"latest": 95, "sma5": 95.0, "sma20": 100.0}
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [], [], [],
        )
        self.assertTrue(all(a["action"] == "hold" for a in decision["actions"]))
        self.assertTrue(decision["next_trigger"]["price_triggers"])
        for t in decision["next_trigger"]["price_triggers"]:
            self.assertIn("above", t)
            self.assertEqual(t["above"], 100.0)

    @patch("rule_engine.price_feed.get_technical_snapshot")
    def test_insufficient_history_skips_symbol(self, mock_snapshot):
        mock_snapshot.return_value = {"latest": 105}  # 沒有sma5/sma20(資料不足)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [], [], [],
        )
        self.assertEqual(decision["actions"], [])
        self.assertEqual(decision["next_trigger"]["price_triggers"], [])


class TestExistingPosition(unittest.TestCase):
    def test_take_profit_closes_regardless_of_sma(self):
        # margin_used = avg_cost*quantity/leverage = 100*1/1 = 100；
        # unrealized_pnl=15 >= 100*0.10=10 → 應停利平倉，即使SMA還沒死亡交叉
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=15.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        closes = [a for a in decision["actions"] if a["action"] == "close"]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["symbol"], "BTC")
        self.assertIn("停利", closes[0]["reasoning"])

    def test_death_cross_closes_when_not_yet_profitable_enough(self):
        # unrealized_pnl=2，margin_used=100，2 < 10門檻，不觸發停利；
        # sma5(95) < sma20(100) → 應死亡交叉平倉
        pos = _position("ETH", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=2.0, sma5=95.0, sma20=100.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        closes = [a for a in decision["actions"] if a["action"] == "close"]
        self.assertEqual(len(closes), 1)
        self.assertIn("死亡交叉", closes[0]["reasoning"])

    def test_holds_when_neither_condition_met(self):
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=3.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        actions_for_btc = [a for a in decision["actions"] if a["symbol"] == "BTC"]
        self.assertEqual(len(actions_for_btc), 1)
        self.assertEqual(actions_for_btc[0]["action"], "hold")
        # 下一次死亡交叉的門檻應設在sma20附近
        triggers = [t for t in decision["next_trigger"]["price_triggers"] if t["symbol"] == "BTC"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].get("below"), 105.0)

    def test_position_not_in_watchlist_still_evaluated(self):
        # SOL不在WATCHLISTS常數裡，但既有持倉仍要能被規則判斷停利/死亡
        # 交叉出場，不能被規則引擎晾在一邊(見rule_engine.py的symbols=
        # 觀察清單∪目前持倉標的 設計)。
        pos = _position("SOL", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=20.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        closes = [a for a in decision["actions"] if a["action"] == "close" and a["symbol"] == "SOL"]
        self.assertEqual(len(closes), 1)


class TestFailSafe(unittest.TestCase):
    @patch("rule_engine.price_feed.get_technical_snapshot")
    def test_never_returns_none_on_normal_run(self, mock_snapshot):
        mock_snapshot.return_value = None  # 完全查無資料
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [], [], [],
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actions"], [])

    def test_unknown_portfolio_id_returns_empty_not_none(self):
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("some_future_account"), [], [], [],
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actions"], [])


class TestStopLossEscalation(unittest.TestCase):
    """AI安全閥(2026-08-04新增)：單部位虧損30%或帳戶回撤30%時臨時問AI要
    不要切損，全部mock掉db/ai_insight，不打真實DB/API。"""

    def setUp(self):
        # 每個測試預設沒有節流紀錄(view as剛開始，沒問過)，個別測試需要
        # 模擬冷卻中的情況再另外override。
        patcher_get_source = patch("rule_engine.db.get_source", return_value=None)
        patcher_upsert = patch("rule_engine.db.upsert_source")
        patcher_record = patch("rule_engine.db.record_fetch_success")
        self.mock_get_source = patcher_get_source.start()
        self.mock_upsert = patcher_upsert.start()
        self.mock_record = patcher_record.start()
        self.addCleanup(patcher_get_source.stop)
        self.addCleanup(patcher_upsert.stop)
        self.addCleanup(patcher_record.stop)

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_single_position_loss_30pct_triggers_ai(self, mock_assess):
        mock_assess.return_value = {"cut": False, "reasoning": "still recoverable"}
        # margin_used=100, unrealized_pnl=-35 -> loss_pct=35% >= 30%門檻
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-35.0, sma5=110.0, sma20=105.0)
        rule_engine.build_trade_decision("angle", _portfolio("crypto_discretionary"), [pos], [], [])
        mock_assess.assert_called_once()
        self.mock_record.assert_called_once()  # 不論AI回什麼，都要標記嘗試時間節流

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_account_drawdown_30pct_triggers_ai_even_if_position_loss_small(self, mock_assess):
        mock_assess.return_value = {"cut": False, "reasoning": "hold"}
        # 單部位只虧5%(不達門檻)，但帳戶總值(現金20+持倉市值30=50)較起始
        # 本金100回撤50% >= 30%門檻，仍應觸發。
        pos = _position("BTC", avg_cost=100.0, quantity=0.3, leverage=1.0,
                         unrealized_pnl=-1.5, market_value=30.0, sma5=110.0, sma20=105.0)
        portfolio = {"portfolio_id": "crypto_discretionary", "current_cash": 20.0,
                     "currency": "USDT", "starting_capital": 100.0}
        rule_engine.build_trade_decision("angle", portfolio, [pos], [], [])
        mock_assess.assert_called_once()

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_ai_says_cut_produces_close_action(self, mock_assess):
        mock_assess.return_value = {"cut": True, "reasoning": "趨勢明顯轉弱，建議切損"}
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-35.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        closes = [a for a in decision["actions"] if a["action"] == "close"]
        self.assertEqual(len(closes), 1)
        self.assertIn("AI安全閥觸發切損", closes[0]["reasoning"])
        self.assertIn("趨勢明顯轉弱", closes[0]["reasoning"])

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_ai_says_no_cut_maintains_hold(self, mock_assess):
        mock_assess.return_value = {"cut": False, "reasoning": "still recoverable"}
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-35.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        actions = [a for a in decision["actions"] if a["symbol"] == "BTC"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "hold")

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_ai_call_failure_maintains_hold_but_still_marks_attempt(self, mock_assess):
        mock_assess.return_value = None  # 呼叫失敗(額度用盡/網路錯誤/格式不對)
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-35.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        actions = [a for a in decision["actions"] if a["symbol"] == "BTC"]
        self.assertEqual(actions[0]["action"], "hold")
        self.mock_record.assert_called_once()  # 失敗仍要標記，避免每20分鐘重打

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_cooldown_skips_ai_call(self, mock_assess):
        # 模擬1小時前才問過(小於4小時冷卻)，這次不該再呼叫AI。
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.mock_get_source.return_value = {"last_fetched_at": recent}
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-35.0, sma5=110.0, sma20=105.0)
        decision = rule_engine.build_trade_decision(
            "angle", _portfolio("crypto_discretionary"), [pos], [], [],
        )
        mock_assess.assert_not_called()
        actions = [a for a in decision["actions"] if a["symbol"] == "BTC"]
        self.assertEqual(actions[0]["action"], "hold")

    @patch("rule_engine.ai_insight.assess_stop_loss")
    def test_below_threshold_never_touches_db_or_ai(self, mock_assess):
        # loss_pct=5%、drawdown=0，都遠低於30%門檻——不該碰db.get_source
        # 也不該呼叫AI，第一層短路判斷要在觸碰db之前就擋下。
        pos = _position("BTC", avg_cost=100.0, quantity=1.0, leverage=1.0,
                         unrealized_pnl=-5.0, sma5=110.0, sma20=105.0)
        rule_engine.build_trade_decision("angle", _portfolio("crypto_discretionary"), [pos], [], [])
        mock_assess.assert_not_called()
        self.mock_get_source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
