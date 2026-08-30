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

CONTRIBUTING_CHANNELS = {
    "us_stock": ("digest_report.us_stock_digest", "美股個股/技術分析統整"),
    "crypto": ("digest_report.crypto_digest", "加密貨幣統整"),
    "macro_tech": ("digest_report.macro_tech_digest", "總經/科技趨勢統整"),
    "geopolitics": ("digest_report.geopolitics_digest", "地緣政治/安全/時事統整"),
    "tsmc": ("digest_report.tsmc_digest", "台積電新聞"),
    "cbc": ("digest_report.cbc_digest", "央行新聞"),
    "macro_fred": ("fred.macro_indicators", "總經指標追蹤"),
    "etf0050": ("stockintelli.tracking", "台股權值股追蹤"),
}

META_SUMMARY_CHANNELS = {
    "tw_stock_meta": {
        "webhook_env": "WEBHOOK_TW_STOCK_META",
        "channel_title": "台股大總結",
        "angle": (
            "綜合研判台股各標的現在是否應進出場、市場情況/情緒(樂觀/悲觀)、"
            "理性面(數據)與實際面(市場反應)是否一致、產業與法人動向，"
            "美股對台股的傳導影響也要納入考量。"
        ),
        "sources": ["etf0050", "tsmc", "cbc", "macro_fred", "macro_tech", "geopolitics", "us_stock"],
    },
    "crypto_meta": {
        "webhook_env": "WEBHOOK_CRYPTO_META",
        "channel_title": "幣圈大總結",
        "angle": (
            "綜合研判幣圈各標的現在是否應進出場、市場情況/情緒(樂觀/悲觀)、"
            "理性面(數據)與實際面(市場反應)是否一致、總經流動性對幣圈的"
            "傳導影響。"
        ),
        "sources": ["crypto", "macro_fred", "macro_tech", "geopolitics"],
    },
}


def run_meta_summary_channel(key: str):
    """大總結頻道：讀取各貢獻頻道當天已產出的報告(不重新抓原始新聞)，
    送Gemini綜合研判進出場/情緒，最多拆2則訊息推播。任一貢獻頻道今天
    沒有報告就跳過該來源，不是整個大總結失敗。"""
    config = META_SUMMARY_CHANNELS[key]
    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    reports = []
    for src_key in config["sources"]:
        source_id, channel_name = CONTRIBUTING_CHANNELS[src_key]
        text = db.get_summary_for_date(source_id, today_str)
        if text:
            reports.append({"channel_name": channel_name, "report_text": text})

    if not reports:
        logger.info("[%s] 今日尚無任何來源頻道報告可供彙整，跳過", key)
        return

    summary = ai_insight.build_meta_summary(config["angle"], reports)
    if not summary:
        logger.error("[%s] Gemini大總結彙整失敗（額度用盡/網路錯誤/回應格式不對），本次略過推播", key)
        return

    embeds, omitted_count = digest_format.build_digest_embeds(summary, config["channel_title"], today_str)
    all_ok = True
    for i, embed in enumerate(embeds):
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 大總結推播成功（訊息 %d/%d）", key, i + 1, len(embeds))
        else:
            all_ok = False
            logger.error("[%s] 大總結推播失敗（訊息 %d/%d）：HTTP %s %s", key, i + 1, len(embeds), status, err)

    if all_ok:
        logger.info(
            "[%s] 完成，彙整 %d 個來源頻道，共 %d 則訊息%s",
            key, len(reports), len(embeds),
            f"（另有{omitted_count}則重點因篇幅省略）" if omitted_count else "",
        )

        # 存檔這次大總結的完整文字,供模擬持倉頻道(run_portfolio_channel)
        # 之後讀取「今天(或最近一次)的大總結研判內容」——比照
        # run_digest_channel()對digest_report.*的既有持久化模式。
        report_text = summary["overview"] + "\n\n" + "\n".join(
            f"【{p['category']}】{p['point']}" for p in summary["points"]
        )
        report_source_id = f"digest_report.{key}"
        db.upsert_source(report_source_id, f"{config['channel_title']}彙整存檔", "digest_report", "")
        saved = db.insert_item_if_new(
            source_id=report_source_id,
            title=f"{config['channel_title']} 彙整（{today_str}）",
            summary=report_text,
            url=webhook_url,
            published_at=today_str,
        )
        if saved:
            db.mark_published(saved["item_id"])


# 每日晨間快報(使用者2026-07-31確認)：早上7:00推播「昨日全日」跨頻道
# 重點摘要，範圍刻意選已經是最上層合成結果的3個來源(tw_stock_meta/
# crypto_meta本身就是彙整過etf0050/tsmc/cbc/macro_fred/macro_tech/
# geopolitics/us_stock/crypto的大總結；semi_supply_chain_digest不在
# 任一大總結的contributing sources裡，故額外納入)，不是重新彙整所有
# 原始頻道——避免跟tw_stock_meta/crypto_meta的內容大量重複，也維持
# prompt大小可控(低成本)。同時推播Discord(新頻道)+Telegram(沿用finfeed
# bot，使用者確認"日報週報在DC應另開頻道"、Telegram沿用既有bot不用
# 新申請)。
