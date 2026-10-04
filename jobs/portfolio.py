"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import json
import logging
import os
from datetime import datetime

import ai_insight
import db
import index_dca_engine
import price_feed
import rule_engine
from push_webhook import send_webhook
import digest_format
from jobs.paths import TAIWAN_TZ

logger = logging.getLogger("main")

# 模擬持倉決策引擎切換點(2026-08-04新增，使用者確認三帳戶改規則式進出場，
# 2026-09-21從擱置47天的分支抽出重新套用到_extract_jobs.py搬過的現在
# 這個位置)。"rules" = rule_engine.build_trade_decision()(SMA5/20交叉，
# 零AI)；"ai" = ai_insight.build_trade_decision()(原本的Gemini自由判斷)。
# 兩邊介面完全相容(同樣的輸入/輸出格式)，改這個常數就能整批切換，AI路徑
# 刻意保留沒刪，之後想比較兩者表現或臨時切回去都不用改程式碼。
DECISION_ENGINE = "rules"

PORTFOLIO_CHANNELS = {
    "tw_stock_portfolio": {
        "portfolio_id": "tw_stock",
        "webhook_env": "WEBHOOK_PORTFOLIO_TW_STOCK",
        "channel_title": "模擬持倉－台股",
        "meta_source_id": "digest_report.tw_semi_digest",
        "angle": "台股現貨帳戶,只能做多(side必須是long),leverage固定為1,不可放空。",
    },
    "crypto_futures_portfolio": {
        "portfolio_id": "crypto_futures",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_FUTURES",
        "channel_title": "模擬持倉－幣圈合約",
        "meta_source_id": "digest_report.crypto_digest",
        "angle": "幣圈合約帳戶,可做多可做空(side可為long或short),可使用槓桿"
                 "(leverage可大於1,但務必評估清算風險,不要無節制放大槓桿)。",
    },
    "crypto_discretionary_portfolio": {
        "portfolio_id": "crypto_discretionary",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_DISCRETIONARY",
        "channel_title": "模擬持倉－幣圈自主判斷",
        "meta_source_id": "digest_report.crypto_digest",
        "angle": "幣圈現貨帳戶,只能做多(side必須是long),leverage固定為1,不可放空、不可用槓桿。",
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
        "market_value": margin_used + pnl,
    }


def _run_tw_stock_dca(key: str, config: dict):
    """tw_stock_portfolio的規則式定期定額(2026-08-06新增，使用者指示，
    見index_dca_engine.py開頭說明)。排程仍是工作日每天跑一次(見
    scripts/setup_scheduled_tasks.ps1)，但本月已經注入過定額(trade_log
    有本月的action='deposit'紀錄)就直接跳過、不重複扣款/不推播——避免
    一個月20幾個工作日各推播一次「本月已完成」造成通知疲勞。"""
    portfolio_id = config["portfolio_id"]
    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    db.init_portfolios()
    trade_date = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
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
        # 買進金額很小(50~100 TWD),實際只買得起零股,用零股當天成交價
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

    final_positions = []
    for p in db.get_open_positions(portfolio_id):
        priced = _price_with_pnl(p)
        final_positions.append(priced if priced is not None else {
            **p, "current_price": "?", "unrealized_pnl": 0,
            "market_value": p["avg_cost"] * p["quantity"] / p["leverage"],
        })

    portfolio = db.get_portfolio(portfolio_id)
    embed = digest_format.build_portfolio_embed(config["channel_title"], trade_date, portfolio, final_positions, action_lines)
    ok, status, err = send_webhook(webhook_url, embed)
    if ok:
        logger.info("[%s] 定期定額推播成功，%d 個動作、%d 筆持倉", key, len(action_lines), len(final_positions))
    else:
        logger.error("[%s] 推播失敗：HTTP %s %s", key, status, err)


def run_portfolio_channel(key: str):
    """模擬持倉頻道：讀取對應大總結報告+目前持倉現價，交給AI決定進出場，
    實際執行(寫db.trade_log/position/portfolio.current_cash)後推播今日
    動作+目前持倉摘要。任一步驟失敗(缺webhook/缺報告/查價失敗/Gemini失敗)
    一律整批跳過，不半套執行——避免「AI決定要交易但價格查不到」這種
    半吊子狀態寫進trade_log。

    tw_stock_portfolio例外：2026-08-06起改用index_dca_engine.py的規則式
    定期定額，完全不呼叫AI，見_run_tw_stock_dca()。"""
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
    recent_reports = db.get_recent_summaries(config["meta_source_id"], limit=5)
    if not recent_reports:
        logger.info("[%s] 尚無可用的大總結報告，跳過", key)
        return
    recent_trades = db.get_recent_trades(portfolio_id, limit=10)
    win_stats = db.get_trade_win_stats(portfolio_id)

    raw_positions = db.get_open_positions(portfolio_id)
    positions = []
    for p in raw_positions:
        priced = _price_with_pnl(p)
        if priced is None:
            logger.error("[%s] %s 查無現價，本次跳過整個帳戶", key, p["symbol"])
            return
        # 技術指標是輔助資訊,查不到不影響本次執行(跟current_price不同,
        # 現價缺了就整批放棄,技術指標缺了只是讓AI少一點參考依據)。
        tech = price_feed.get_technical_snapshot(p["symbol"])
        if tech:
            priced.update({k: v for k, v in tech.items() if k != "latest"})
        positions.append(priced)

    engine = rule_engine if DECISION_ENGINE == "rules" else ai_insight
    decision = engine.build_trade_decision(config["angle"], portfolio, positions, recent_trades, recent_reports, win_stats)
    if not decision:
        logger.error("[%s] %s決策失敗（額度用盡/網路錯誤/回應格式不對），本次跳過", key, DECISION_ENGINE)
        # AI is optional: preserve the deterministic core with a safe HOLD.
        logger.warning("[%s] AI unavailable; degrading to deterministic HOLD", key)
        decision = {
            "actions": [
                {"action": "hold", "symbol": p.get("symbol", ""),
                 "side": p.get("side", "long"), "cash_ratio": 0.0,
                 "leverage": 1.0, "position_id": p.get("position_id", ""),
                 "reasoning": "AI unavailable; no position change"}
                for p in positions
            ],
            "next_trigger": {},
        }

    trade_date = datetime.now().strftime("%Y-%m-%d")
    is_crypto_futures = portfolio_id == "crypto_futures"
    action_lines = []

    for a in decision["actions"]:
        if a["action"] == "hold":
            reasoning = a["reasoning"] or "(無說明)"
            db.record_hold(portfolio_id, trade_date, reasoning)
            # 不管有沒有綁定特定標的都要顯示理由——原本只在有symbol時才加進
            # action_lines，導致「整體觀望、不特定標的」這種hold的理由被
            # 寫進db卻不會出現在Discord訊息裡，使用者只看到「0個動作」卻
            # 不知道AI為什麼不動作(2026-07-30發現)。
            if a["symbol"]:
                action_lines.append(f"• 持有 {a['symbol']}：{reasoning}")
            else:
                action_lines.append(f"• 觀望：{reasoning}")
            continue

        if a["action"] == "close":
            match = next((p for p in positions if p["position_id"] == a["position_id"]), None)
            if match is None:
                logger.warning("[%s] AI指定平倉的position_id不存在，忽略此動作", key)
                continue
            try:
                pnl = db.close_position(match["position_id"], match["current_price"], trade_date, a["reasoning"])
            except ValueError as e:
                # 防止AI決策JSON意外重複同一筆close動作(見db.close_position()
                # 的status防呆)導致整個函式崩潰、當次完全不推播——單一動作
                # 失敗只跳過該動作,不影響同一批次其餘動作跟最終推播。
                logger.warning("[%s] 平倉失敗，忽略此動作：%s", key, e)
                continue
            action_lines.append(
                f"• 平倉 {match['symbol']}（現價{match['current_price']:g}）"
                f" 已實現損益{pnl:+,.2f}：{a['reasoning']}"
            )
            continue

        if a["action"] == "open":
            if not a["symbol"] or a["cash_ratio"] <= 0:
                continue
            side = a["side"] if is_crypto_futures and a["side"] in ("long", "short") else "long"
            leverage = a["leverage"] if is_crypto_futures and a["leverage"] > 1 else 1.0
            price = price_feed.get_price(a["symbol"])
            if price is None or price <= 0:
                logger.warning("[%s] %s 查無現價，忽略此開倉動作", key, a["symbol"])
                continue

            fresh_cash = db.get_portfolio(portfolio_id)["current_cash"]
            cash_ratio = min(a["cash_ratio"], 1.0)
            margin_used = fresh_cash * cash_ratio
            if margin_used <= 0:
                continue
            quantity = margin_used * leverage / price
            db.open_position(portfolio_id, a["symbol"], side, quantity, price, leverage, trade_date, a["reasoning"])
            action_lines.append(
                f"• 開倉 {a['symbol']} {side} 數量{quantity:g}（價{price:g}，"
                f"槓桿{leverage:g}x，動用現金{margin_used:,.2f}）：{a['reasoning']}"
            )

    # 事件觸發機制(2026-07-31,使用者APPROVED,07-31再次確認擴及discretionary)：
    # crypto_futures_portfolio/crypto_discretionary_portfolio都改成
    # event-triggered排程(見check_triggers.py)，只有tw_stock_portfolio維持
    # 原本排程不變，故next_trigger只在這兩個帳戶持久化。
    if key in ("crypto_futures_portfolio", "crypto_discretionary_portfolio"):
        next_trigger = decision.get("next_trigger") or {}
        existing = db.get_portfolio_trigger(portfolio_id)
        min_hours = existing["min_hours_between_calls"] if existing else 168.0
        latest_trades = db.get_recent_trades(portfolio_id, limit=1)
        latest_log_id = latest_trades[0]["log_id"] if latest_trades else None
        db.set_portfolio_trigger(
            portfolio_id,
            json.dumps(next_trigger.get("price_triggers", [])),
            json.dumps(next_trigger.get("news_keywords", [])),
            min_hours,
            latest_log_id,
        )
        logger.info("[%s] 已更新下次觸發條件：%s", key, next_trigger.get("reasoning", "(無說明)"))

    portfolio = db.get_portfolio(portfolio_id)
    final_positions = []
    for p in db.get_open_positions(portfolio_id):
        priced = _price_with_pnl(p)
        # 交易已經執行完了,查價失敗只影響報告顯示,不能因此不推播——
        # 用avg_cost頂替current_price純粹是顯示用途(market_value退回margin_used,
        # 不假裝算得出unrealized_pnl),不影響db裡任何已寫入的數字。
        final_positions.append(priced if priced is not None else {
            **p, "current_price": "?", "unrealized_pnl": 0,
            "market_value": p["avg_cost"] * p["quantity"] / p["leverage"],
        })

    embed = digest_format.build_portfolio_embed(config["channel_title"], trade_date, portfolio, final_positions, action_lines)
    ok, status, err = send_webhook(webhook_url, embed)
    if ok:
        logger.info("[%s] 推播成功，%d 個動作、%d 筆持倉", key, len(action_lines), len(final_positions))
    else:
        logger.error("[%s] 推播失敗：HTTP %s %s", key, status, err)
