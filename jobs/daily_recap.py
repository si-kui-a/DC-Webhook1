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

logger = logging.getLogger("main")

DAILY_RECAP_SOURCES = [
    ("digest_report.tw_stock_meta", "台股大總結"),
    ("digest_report.crypto_meta", "幣圈大總結"),
    ("digest_report.semi_supply_chain_digest", "半導體供應鏈"),
]


def run_daily_recap():
    """每日晨間快報：讀取昨天(不是今天——這是早上7點跑的T+1晨報，各
    大總結頻道是前一晚才產出報告)已產出的報告，送Gemini壓縮成「只提供
    明確重點」的精簡摘要，推播到Discord新頻道+Telegram(finfeed bot)。"""
    key = "daily_recap"
    webhook_url = os.getenv("WEBHOOK_DAILY_RECAP")
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 WEBHOOK_DAILY_RECAP，跳過", key)
        return

    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    reports = []
    for source_id, channel_name in DAILY_RECAP_SOURCES:
        text = db.get_summary_for_date(source_id, yesterday_str)
        if text:
            reports.append({"channel_name": channel_name, "report_text": text})

    if not reports:
        logger.info("[%s] 昨日(%s)尚無任何來源頻道報告可供彙整，跳過", key, yesterday_str)
        return

    angle = (
        "把過去一天的重點濃縮成明確、精簡的摘要，不做投資建議、不需要"
        "詳盡的分析過程，讀者要能在幾秒內抓到「昨天發生了什麼重要的事」。"
    )
    summary = ai_insight.build_meta_summary(angle, reports)
    if not summary:
        logger.error("[%s] Gemini彙整失敗（額度用盡/網路錯誤/回應格式不對），本次略過推播", key)
        return

    channel_title = "每日晨間快報"
    embeds, omitted_count = digest_format.build_digest_embeds(summary, channel_title, yesterday_str)
    all_ok = True
    for i, embed in enumerate(embeds):
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 推播成功（訊息 %d/%d）", key, i + 1, len(embeds))
        else:
            all_ok = False
            logger.error("[%s] 推播失敗（訊息 %d/%d）：HTTP %s %s", key, i + 1, len(embeds), status, err)

    tg_points = "\n".join(f"• 【{p['category']}】{p['point']}" for p in summary["points"])
    tg_text = (
        f"☀️ *每日晨間快報（{yesterday_str}）*\n\n"
        f"{summary['overview']}\n\n{tg_points}\n\n"
        f"完整內容請至 Discord #每日晨間快報 查看"
    )
    notify_telegram.send_message(tg_text)

    if all_ok:
        logger.info(
            "[%s] 完成，彙整 %d 個來源，共 %d 則Discord訊息%s",
            key, len(reports), len(embeds),
            f"（另有{omitted_count}則重點因篇幅省略）" if omitted_count else "",
        )


# 模擬持倉(紙上帳戶,使用者確認2026-07-30,見schema.sql同段落註解)：全部是
# 模擬交易，不動用真實資金。3個帳戶各自獨立頻道(使用者確認2026-07-30)，
# 不共用webhook——避免3個帳戶的動作/持倉訊息混在同一個頻道裡難以分辨。
#
# tw_stock_portfolio(2026-08-06起改版，使用者指示)：原本跟另外兩個帳戶
# 一樣交給AI(Gemini)即時判斷個股進出場，但自2026-07-30建立以來從未真正
# 買進過(全部觀望)。改為index_dca_engine.py的規則式指數ETF(0050/006208)
# 定期定額，完全不呼叫AI，見run_portfolio_channel()裡的tw_stock分流。
# meta_source_id/angle兩個欄位對AI路徑才有意義，tw_stock已不使用。
#
# crypto_futures_portfolio/crypto_discretionary_portfolio(未改版)：讀取
# 對應大總結頻道「最近幾次」已產出的報告(不限定當天——tw_stock_meta於
# 台股收盤後13:30左右執行,當天晚上20:30才會有新報告,收盤時點只有前一晚
# 的報告可用,見db.get_recent_summaries()；PAT-15 enrichment之後改用這個
# 而非單筆的get_latest_summary())，交給AI決定進出場。
