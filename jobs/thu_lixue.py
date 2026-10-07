"""jobs/thu_lixue.py — 東海大學勵學基金申請時程的Telegram提醒(2026-10-07新增)。

每天跑一次(scripts/cloud_scheduler.py)，用EDU bot(跟jobs/thu_calendar.py同一個
對話)。兩種訊息，沒有事項就完全不推播：

1. 時程提醒：公告裡抽出的每個日期(開放、截止、執行期限)，在前兩個月、前一個月、
   前一週、前一天各提醒一次(REMIND_DAYS_BEFORE，使用者2026-10-07指定)。
2. 新公告：公告日期是昨天的勵學基金公告，隔天推一次，附上抽到的日期。公告晚
   於某個提醒點才出來(例如截止前三週才公告)時，前面的提醒點已經錯過，這則
   訊息就是補上的那一次；抽不到日期的公告也會在這裡出現，不會靜默漏掉。

不存狀態檔：雲端排程每次都是乾淨環境，兩種訊息都只由「今天」決定。
"""
from __future__ import annotations

import html
import logging
from datetime import date, timedelta

import notify_telegram
from scrapers import thu_lixue

logger = logging.getLogger("main")

REMIND_DAYS_BEFORE = (60, 30, 7, 1)
_WHEN = {60: "前兩個月", 30: "前一個月", 7: "前一週", 1: "明天"}
_KIND = {"start": "開始", "end": "截止", "deadline": "截止", "date": ""}


def _event_text(event: dict, ann: dict) -> str:
    kind = _KIND[event["kind"]]
    name = f"{thu_lixue.short_title(ann['title'])}｜{event['label']}"
    return f"{event['date'].isoformat()}{('（' + kind + '）') if kind else ''} {html.escape(name)}"


def due_reminders(announcements: list[dict], today: date) -> list[tuple[int, dict, dict]]:
    """[(提前天數, 事項, 公告)]：今天剛好是某事項前 60/30/7/1 天。同一天同說明的事項只留一筆
    (同一則時程常在好幾則公告重複出現)。"""
    out, seen = [], set()
    for ann in announcements:
        for e in ann["events"]:
            days = (e["date"] - today).days
            key = (e["date"], e["kind"], e["label"])
            if days in REMIND_DAYS_BEFORE and key not in seen:
                seen.add(key)
                out.append((days, e, ann))
    return sorted(out, key=lambda x: (x[0], x[1]["date"]))


def new_announcements(announcements: list[dict], today: date) -> list[dict]:
    yesterday = (today - timedelta(days=1)).isoformat()
    return [a for a in announcements if a["date"] == yesterday]


def build_message(announcements: list[dict], today: date) -> str | None:
    reminders = due_reminders(announcements, today)
    fresh = new_announcements(announcements, today)
    if not reminders and not fresh:
        return None
    lines = ["🎓 <b>東海勵學基金提醒</b>"]
    if reminders:
        lines.append("")
        for days, event, ann in reminders:
            lines.append(f"• [{_WHEN[days]}] {_event_text(event, ann)}")
            lines.append(f"  {ann['url']}")
    for ann in fresh:
        upcoming = [e for e in ann["events"] if e["date"] >= today]
        lines += ["", f"🆕 新公告：{html.escape(ann['title'])}", ann["url"]]
        lines += [f"  • {_event_text(e, ann)}" for e in sorted(upcoming, key=lambda e: e["date"])]
        if not upcoming:
            lines.append("  （沒有抽到之後的日期，請點連結看內容）")
    return "\n".join(lines)


def run_thu_lixue(today: date | None = None) -> bool:
    today = today or date.today()
    try:
        announcements = thu_lixue.fetch_announcements(today)
    except Exception as e:
        # http_client已重試；抓不到當天就不推，隔天照常(提醒點錯過一個，其他三個還在)
        logger.error("[thu_lixue] 抓取失敗，今天不推播: %s", e)
        return False
    message = build_message(announcements, today)
    if message is None:
        logger.info("[thu_lixue] 今天沒有要提醒的事項（%d 則公告）", len(announcements))
        return True
    ok = notify_telegram.send_message(
        message, parse_mode="HTML",
        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID,
    )
    if ok:
        logger.info("[thu_lixue] Telegram提醒推播成功")
    else:
        logger.error("[thu_lixue] Telegram提醒推播失敗")
    return ok
