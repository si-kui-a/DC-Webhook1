"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import logging
import os
from datetime import datetime, timedelta

import ai_insight
import db
import notify_telegram
from push_webhook import send_webhook
import digest_format

logger = logging.getLogger("main")

# 2026-10-04：台股/幣圈大總結刪除(那是AI再摘要AI的產出，原本用途是餵AI
# 交易決策，交易已改規則式)，改直接讀3個合併後的彙整頻道，加上原本只透過
# 大總結間接進來的兩個規則式報告(FRED總經指標、0050權值股追蹤)。
DAILY_RECAP_SOURCES = [
    ("digest_report.us_macro_digest", "美股與總經"),
    ("digest_report.tw_semi_digest", "台股與半導體"),
    ("digest_report.crypto_digest", "加密貨幣"),
    ("fred.macro_indicators", "總經指標追蹤"),
    ("stockintelli.tracking", "台股權值股追蹤"),
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
        return False

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


# 模擬持倉(紙上帳戶，不動用真實資金)的流程見 jobs/portfolio.py 開頭說明。
