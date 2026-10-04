"""模擬持倉的執行層：查價、交給 rule_engine 決策、寫入 db、推播 Discord。

加密貨幣兩帳戶由 check_triggers.py 每輪（雲端排程每小時）呼叫 run_portfolio_channel()，
只有真的開倉／平倉才推播；每日摘要由 jobs/crypto_recap.py 推。台股帳戶是每月定期定額
（index_dca_engine），完全不呼叫 AI。

2026-10-04 起交易決策完全不用 AI（見 rule_engine.py 開頭說明）；之前的 AI 決策路徑、
價格觸發條件、每輪寫一筆 hold_update 都已移除。
"""
import logging
import os
from datetime import date, datetime, timezone

import db
import index_dca_engine
import portfolio_metrics
import price_feed
import rule_engine
from push_webhook import send_webhook
import digest_format
from jobs.paths import TAIWAN_TZ

logger = logging.getLogger("main")

PORTFOLIO_CHANNELS = {
    "tw_stock_portfolio": {
        "portfolio_id": "tw_stock",
        "webhook_env": "WEBHOOK_PORTFOLIO_TW_STOCK",
        "channel_title": "模擬持倉－台股",
    },
    "crypto_futures_portfolio": {
        "portfolio_id": "crypto_futures",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_FUTURES",
        "channel_title": "模擬持倉－幣圈合約",
    },
    "crypto_discretionary_portfolio": {
        "portfolio_id": "crypto_discretionary",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_DISCRETIONARY",
        "channel_title": "模擬持倉－幣圈現貨無槓桿",
    },
}


def _price_with_pnl(position: dict) -> dict | None:
    """幫position補上current_price/unrealized_pnl/market_value,查價失敗
    回傳None,呼叫端須整批放棄本次執行——缺價無法正確算PnL,不可用avg_cost
    或0頂替(會讓數字失真,違反PnL公式須先核對的規則)。"""
    price = price_feed.get_price(position["symbol"])
    if price is None:
        return None
    margin_used = position["avg_cost"] * position["quantity"] / position["leverage"]
    if position["side"] == "short":
        pnl = (position["avg_cost"] - price) * position["quantity"]
    else:
        pnl = (price - position["avg_cost"]) * position["quantity"]
    return {
        **position,
        "current_price": price,
        "unrealized_pnl": pnl,
        "market_value": max(0.0, margin_used + pnl),  # 強制平倉後保證金歸零，不會是負數
    }


def priced_positions_for_report(portfolio_id: str) -> list[dict]:
    """推播用的持倉清單：交易已經寫完，查價失敗只影響顯示，不能因此不推播。"""
    out = []
    for p in db.get_open_positions(portfolio_id):
        priced = _price_with_pnl(p)
        out.append(priced if priced is not None else {
            **p, "current_price": "?", "unrealized_pnl": 0,
            "market_value": p["avg_cost"] * p["quantity"] / p["leverage"],
        })
    return out


def _created_date(portfolio: dict) -> date:
    return datetime.fromisoformat(portfolio["created_at"]).astimezone(TAIWAN_TZ).date()


def xirr_line(portfolio: dict, deposits: list[dict], total_value: float, today: date) -> str | None:
    """定期定額看資金加權報酬(XIRR)：每筆入金依時間點計入。"""
    start = _created_date(portfolio)
    flows = [(start, -portfolio["starting_capital"])]
    flows += [(date.fromisoformat(d["trade_date"]), -d["amount"]) for d in deposits]
    flows.append((today, total_value))
    rate = portfolio_metrics.xirr(flows)
    if rate is None:
        return None
    note = "（未滿一年，年化數字波動大）" if (today - start).days < 365 else ""
    return f"資金加權年化報酬(XIRR):{rate:+.1%}{note}"


def _run_tw_stock_dca(key: str, config: dict):
    """tw_stock_portfolio的規則式定期定額(2026-08-06新增，使用者指示，
    見index_dca_engine.py開頭說明)。排程仍是工作日每天跑一次(見
    .github/workflows/scheduler.yml)，但本月已經注入過定額(trade_log
    有本月的action='deposit'紀錄)就直接跳過、不重複扣款/不推播——避免
    一個月20幾個工作日各推播一次「本月已完成」造成通知疲勞。"""
    portfolio_id = config["portfolio_id"]
    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    db.init_portfolios()
    today = datetime.now(TAIWAN_TZ).date()
    trade_date = today.isoformat()
    this_month = trade_date[:7]  # YYYY-MM

    recent_trades = db.get_recent_trades(portfolio_id, limit=10)
    if any(t["action"] == "deposit" and t["trade_date"][:7] == this_month for t in recent_trades):
        logger.info("[%s] 本月定期定額已執行過，跳過", key)
        return

    contribution_reasoning = f"每月定期定額注入{index_dca_engine.MONTHLY_CONTRIBUTION:.0f} TWD"
    db.deposit_cash(portfolio_id, index_dca_engine.MONTHLY_CONTRIBUTION, trade_date, contribution_reasoning)
    action_lines = [f"• 入金：{contribution_reasoning}"]

    portfolio = db.get_portfolio(portfolio_id)
    technical_snapshots = {
        symbol: price_feed.get_technical_snapshot(symbol) for symbol in index_dca_engine.TARGET_SYMBOLS
    }
    plans = index_dca_engine.decide_monthly_buys(portfolio["current_cash"], technical_snapshots)

    for plan in plans:
        if plan["amount"] <= 0:
            action_lines.append(f"• {plan['symbol']}：本月暫停加碼（{plan['reasoning']}）")
            continue
        # 買進金額很小(50~200 TWD),實際只買得起零股,用零股當天成交價
        # (2026-08-06使用者指示)；當天該檔無零股成交時退回整股收盤價，
        # 不可用0頂替(會讓quantity/PnL計算失真)。
        price = price_feed.get_odd_lot_price(plan["symbol"])
        price_source = "零股"
        if price is None or price <= 0:
            price = price_feed.get_price(plan["symbol"])
            price_source = "整股(當日無零股成交)"
        if price is None or price <= 0:
            logger.warning("[%s] %s 查無現價，本次跳過此標的", key, plan["symbol"])
            action_lines.append(f"• {plan['symbol']}：查無現價，本次跳過")
            continue
        quantity = plan["amount"] / price
        db.open_position(portfolio_id, plan["symbol"], "long", quantity, price, 1.0, trade_date, plan["reasoning"])
        action_lines.append(
            f"• 買進 {plan['symbol']} 數量{quantity:g}（{price_source}價{price:g}，"
            f"金額{plan['amount']:,.2f}）：{plan['reasoning']}"
        )

    final_positions = priced_positions_for_report(portfolio_id)
    portfolio = db.get_portfolio(portfolio_id)
    deposits = db.get_deposits(portfolio_id)
    total_value = portfolio["current_cash"] + sum(p.get("market_value", 0) for p in final_positions)
    extra = [line for line in [xirr_line(portfolio, deposits, total_value, today)] if line]
    embed = digest_format.build_portfolio_embed(
        config["channel_title"], trade_date, portfolio, final_positions, action_lines,
        deposits=sum(d["amount"] for d in deposits), extra_lines=extra,
    )
    ok, status, err = send_webhook(webhook_url, embed)
    if ok:
        logger.info("[%s] 定期定額推播成功，%d 個動作、%d 筆持倉", key, len(action_lines), len(final_positions))
    else:
        logger.error("[%s] 推播失敗：HTTP %s %s", key, status, err)


def run_portfolio_channel(key: str):
    """加密貨幣帳戶跑一輪規則：查價→rule_engine決策→寫db；有開倉或平倉才推播。
    任一持倉查不到現價就整輪跳過，不半套執行。tw_stock_portfolio 走 _run_tw_stock_dca()。"""
    config = PORTFOLIO_CHANNELS[key]
    portfolio_id = config["portfolio_id"]

    if portfolio_id == "tw_stock":
        _run_tw_stock_dca(key, config)
        return

    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    db.init_portfolios()
    portfolio = db.get_portfolio(portfolio_id)
    positions = []
    for p in db.get_open_positions(portfolio_id):
        priced = _price_with_pnl(p)
        if priced is None:
            logger.error("[%s] %s 查無現價，本次跳過整個帳戶", key, p["symbol"])
            return
        positions.append(priced)

    symbols = set(rule_engine.WATCHLISTS.get(portfolio_id, [])) | {p["symbol"] for p in positions}
    snapshots = {s: price_feed.get_technical_snapshot(s) for s in symbols}
    # Binance 日線在 UTC 00:00 收盤：這之後交易過的標的，代表已經對這根訊號 K 線動作過
    last_close = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    traded = db.get_symbols_traded_since(portfolio_id, last_close.isoformat())
    actions = rule_engine.build_trade_decision(portfolio, positions, snapshots, traded)

    trade_date = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    action_lines = []
    for a in actions:
        if a["action"] == "close":
            match = next(p for p in positions if p["position_id"] == a["position_id"])
            price = match["current_price"]
            costs = rule_engine.trade_costs(portfolio_id, match, price, rule_engine.held_days(match))
            try:
                pnl = db.close_position(match["position_id"], price, trade_date, a["reasoning"],
                                        costs=costs, liquidated=a["kind"] == "liquidation")
            except ValueError as e:
                logger.warning("[%s] 平倉失敗，忽略此動作：%s", key, e)
                continue
            action_lines.append(f"• 平倉 {match['symbol']}（現價{price:g}）已實現損益{pnl:+,.2f}：{a['reasoning']}")
        elif a["action"] == "open":
            price = price_feed.get_price(a["symbol"])
            if price is None or price <= 0:
                logger.warning("[%s] %s 查無現價，忽略此開倉動作", key, a["symbol"])
                continue
            margin_used = db.get_portfolio(portfolio_id)["current_cash"] * min(a["cash_ratio"], 1.0)
            if margin_used <= 0:
                continue
            quantity = margin_used * a["leverage"] / price
            db.open_position(portfolio_id, a["symbol"], a["side"], quantity, price, a["leverage"], trade_date, a["reasoning"])
            action_lines.append(
                f"• 開倉 {a['symbol']} {a['side']} 數量{quantity:g}（價{price:g}，"
                f"槓桿{a['leverage']:g}x，動用現金{margin_used:,.2f}）：{a['reasoning']}"
            )

    if not action_lines:
        logger.info("[%s] 本輪無進出場", key)
        return

    portfolio = db.get_portfolio(portfolio_id)
    embed = digest_format.build_portfolio_embed(
        config["channel_title"], trade_date, portfolio, priced_positions_for_report(portfolio_id), action_lines,
    )
    ok, status, err = send_webhook(webhook_url, embed)
    if ok:
        logger.info("[%s] 推播成功，%d 個動作", key, len(action_lines))
    else:
        logger.error("[%s] 推播失敗：HTTP %s %s", key, status, err)
