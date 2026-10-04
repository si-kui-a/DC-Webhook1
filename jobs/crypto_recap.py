"""加密貨幣模擬持倉的每日摘要（每晚 21:00，純規則、零 AI）。

2026-10-04 起每晚一定推一則：當天的開倉／平倉、目前持倉、扣除入金後的報酬，以及同期
「觀察清單各半直接持有」的對照。之前只在「今天沒被觸發過」時推「今日無變動」；觸發機制
移除後，盤中只有真的交易才推播，持倉概況改由這則摘要每天固定提供。
"""
import logging
import os
from datetime import datetime

import db
import price_feed
import rule_engine
from push_webhook import send_webhook
import digest_format
from jobs.paths import TAIWAN_TZ
from jobs.portfolio import PORTFOLIO_CHANNELS, priced_positions_for_report

logger = logging.getLogger("main")

CRYPTO_PORTFOLIO_KEYS = ["crypto_futures_portfolio", "crypto_discretionary_portfolio"]


def buy_and_hold_line(portfolio: dict, symbols: list[str]) -> str | None:
    """起始本金在帳戶建立當天平均買進觀察清單、一路持有到現在的價值。"""
    created = datetime.fromisoformat(portfolio["created_at"])
    start_ms = int(created.timestamp() * 1000)
    ratios = []
    for s in symbols:
        bars = price_feed.get_crypto_daily_bars(s, start_ms=start_ms, limit=1)
        now = price_feed.get_price(s)
        if not bars or not now:
            return None
        ratios.append(now / bars[0]["open"])
    value = portfolio["starting_capital"] * sum(ratios) / len(ratios)
    ret = value / portfolio["starting_capital"] - 1
    return f"對照:同期{'/'.join(symbols)}各半買進持有 {value:,.2f}（{ret:+.1%}）"


def run_crypto_nightly_recap():
    date_str = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    for key in CRYPTO_PORTFOLIO_KEYS:
        config = PORTFOLIO_CHANNELS[key]
        portfolio_id = config["portfolio_id"]
        webhook_url = os.getenv(config["webhook_env"])
        if not webhook_url:
            logger.error("[%s] 缺少環境變數 %s，跳過每日摘要", key, config["webhook_env"])
            continue
        portfolio = db.get_portfolio(portfolio_id)
        if not portfolio:
            logger.info("[%s] 尚無模擬持倉資料，跳過每日摘要", key)
            continue

        trades = db.get_trades_on(portfolio_id, date_str)
        lines = [
            f"• {'平倉' if t['action'] == 'close' else '開倉'} {t['symbol']} 價{t['price']:g}"
            + (f" 已實現損益{t['pnl']:+,.2f}" if t["pnl"] is not None else "")
            for t in trades
        ] or ["今日無進出場"]
        bh = buy_and_hold_line(portfolio, rule_engine.WATCHLISTS.get(portfolio_id, []))
        embed = digest_format.build_portfolio_embed(
            f"{config['channel_title']}（每日摘要）", date_str, portfolio,
            priced_positions_for_report(portfolio_id), lines, extra_lines=[bh] if bh else None,
        )
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 每日摘要推播成功", key)
        else:
            logger.error("[%s] 每日摘要推播失敗：HTTP %s %s", key, status, err)
