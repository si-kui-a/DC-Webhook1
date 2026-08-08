import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest_format


class PortfolioEmbedTests(unittest.TestCase):
    def test_payload_shape_and_limits(self):
        portfolio = {"current_cash": 8000, "starting_capital": 10000, "currency": "USD"}
        position = {
            "symbol": "BTC", "side": "long", "quantity": 0.1, "avg_cost": 64000,
            "market_value": 6500, "current_price": 65000,
            "unrealized_pnl": 250,
        }
        embed = digest_format.build_portfolio_embed(
            "Test", "2026-08-08", portfolio, [position], ["HOLD"]
        )
        self.assertEqual(len(embed["fields"]), 3)
        self.assertLessEqual(len(embed["title"]), 256)
        for field in embed["fields"]:
            self.assertLessEqual(len(field["value"]), 1024)


if __name__ == "__main__":
    unittest.main()
