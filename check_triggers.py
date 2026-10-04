"""
check_triggers.py — 每輪（雲端排程每小時，scripts/cloud_scheduler.py 的 EVERY_TICK）跑一次
兩個加密貨幣模擬帳戶的規則決策；有開倉或平倉才推播，每日摘要另由 crypto_nightly_recap 推。

檔名沿用舊名，是因為排程設定與歷史紀錄都用這個名字。2026-10-04 之前這裡是價格／消息／
市場三種觸發條件加每週保底，用來節省 AI 決策的額度；交易決策改成零 AI 的規則
（rule_engine.py）之後，觸發機制沒有存在理由，而且它只看「價格在線的哪一側」，價格停在
另一側時每輪都觸發（09-24/25 每小時寫進 6 筆持有紀錄）。現在每輪直接跑規則。

Usage: python check_triggers.py
Exit 1 if any account raised.
"""
import logging
import sys

import main

PORTFOLIO_KEYS = ["crypto_futures_portfolio", "crypto_discretionary_portfolio"]
logger = logging.getLogger("check_triggers")


def run_all() -> int:
    failed = 0
    for key in PORTFOLIO_KEYS:
        try:
            main.run_portfolio_channel(key)
        except Exception:
            logger.exception("[%s] 規則決策失敗", key)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run_all())
