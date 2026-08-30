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

def run_sig_content_watch():
    """留德網站(sig)內容維護監測，見scrapers/sig_content_watch.py開頭的
    範圍說明——只做死連結檢查+updated_at過期提醒，季排程。沒有異常時
    完全不推播(避免每週固定「一切正常」造成通知疲勞)。"""
    key = sig_content_watch.SOURCE_ID
    message = sig_content_watch.run_check()
    if message is None:
        logger.info("[%s] 本次檢查無異常，不推播", key)
        return

    ok = notify_telegram.send_message(
        message,
        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID,
    )
    if ok:
        logger.info("[%s] 推播成功", key)
    else:
        logger.error("[%s] 推播失敗", key)


# 跟check_triggers.py的PORTFOLIO_KEYS同樣內容，但獨立定義不匯入該模組——
# check_triggers.py會import main，main.py若反過來import check_triggers
# 會形成循環匯入，兩個字串重複維護比繞開循環匯入的成本低。
