"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import ai_insight
import db
import index_dca_engine
import price_feed
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050, macro_fred, twse_financials, rental_search
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import substack_generic
from scrapers import semi_supply_chain
import digest_format
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util
from scrapers import internship_mol
from scrapers import internship_104
from scrapers import internship_rich
from scrapers import internship_yes123
from scrapers import internship_gift
from scrapers import internship_util
from scrapers import sig_content_watch
from jobs.paths import TAIWAN_TZ
from jobs.portfolio import PORTFOLIO_CHANNELS, _price_with_pnl

logger = logging.getLogger("main")

CRYPTO_PORTFOLIO_KEYS = ["crypto_futures_portfolio", "crypto_discretionary_portfolio"]


def run_crypto_nightly_recap():
    """加密貨幣模擬持倉的晚間彙整(2026-07-31使用者確認)：純規則,零AI,
    每晚固定時間跑一次。今天已經因check_triggers.py觸發過AI決策的帳戶
    (不管是真的市場觸發、還是首次執行)不重複推播，避免內容重複；今天
    完全沒被觸發過的帳戶才推播一則「今日無變動」通知(含目前持倉現況)，
    讓使用者確認系統仍在正常運作，不是排程掛掉導致沒有任何動靜。"""
    date_str = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    for key in CRYPTO_PORTFOLIO_KEYS:
        config = PORTFOLIO_CHANNELS[key]
        portfolio_id = config["portfolio_id"]
        webhook_url = os.getenv(config["webhook_env"])
        if not webhook_url:
            logger.error("[%s] 缺少環境變數 %s，跳過晚間無變動彙整", key, config["webhook_env"])
            continue

        trigger = db.get_portfolio_trigger(portfolio_id)
        if trigger:
            set_at = datetime.fromisoformat(trigger["set_at"])
            if set_at.tzinfo is None:
                set_at = set_at.replace(tzinfo=timezone.utc)
            if set_at.astimezone(TAIWAN_TZ).date() == datetime.now(TAIWAN_TZ).date():
                logger.info("[%s] 今天已觸發過AI決策，晚間彙整不重複推播", key)
                continue

        portfolio = db.get_portfolio(portfolio_id)
        if not portfolio:
            logger.info("[%s] 尚無模擬持倉資料，跳過晚間彙整", key)
            continue

        final_positions = []
        for p in db.get_open_positions(portfolio_id):
            priced = _price_with_pnl(p)
            final_positions.append(priced if priced is not None else {
                **p, "current_price": "?", "unrealized_pnl": 0,
                "market_value": p["avg_cost"] * p["quantity"] / p["leverage"],
            })

        embed = digest_format.build_portfolio_embed(
            f"{config['channel_title']}（今日無變動）", date_str, portfolio, final_positions,
            ["今日策略進出場無變動"],
        )
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 晚間無變動彙整推播成功", key)
        else:
            logger.error("[%s] 晚間無變動彙整推播失敗：HTTP %s %s", key, status, err)
