"""jobs/thu_calendar.py — 東海大學當期學期行事曆合併到倉庫+Telegram提醒
(2026-09-10新增，同日追加Telegram提醒推播)。

取代原本教育類(scholarship/internship)的Telegram通知(使用者2026-09-10
確認拿掉)：EDU bot改以這支job的東海行事曆提醒為主要用途(使用者同日再
確認)。注意sig_watch.py(留德網站內容監測)仍照原樣繼續共用同一組EDU
bot token/chat_id發自己的訊息(季排程，內容異常時才發)——同一個Telegram
對話裡偶爾出現的訊息不一定都跟行事曆有關，不要假設全部都是。

兩件事各自獨立執行、互不影響：
1. 把scrapers/thu_calendar.py抓到的當期學期事件寫成
   data/thu_academic_calendar.json(本機檔，2026-09-25起不進版控，
   見docs/DECISIONS.md)。
2. 依scrapers/thu_calendar.py的get_reminder_trigger_dates()判斷「今天」
   是否有事項該提醒，有才發一則Telegram訊息(沒有就完全不推播，避免
   通知疲勞，比照sig_watch.py的既有慣例)。訊息內容除了今天要提醒的
   事項，還會附上「下一次推播」的日期跟事項(使用者2026-09-10確認)。
"""
import json
import logging
from datetime import date

import notify_telegram
from jobs.paths import THU_CALENDAR_PATH
from scrapers import thu_calendar as thu_calendar_scraper
from scrapers.thu_calendar import get_current_semester_calendar

logger = logging.getLogger("main")


def _events_due_today(events: list[dict], today: date) -> list[tuple[date, dict]]:
    due = []
    for e in events:
        event_date = date.fromisoformat(e["date"])
        if today in thu_calendar_scraper.get_reminder_trigger_dates(event_date, e["title"]):
            due.append((event_date, e))
    return due


def _next_push(events: list[dict], today: date) -> tuple[date | None, list[dict]]:
    """回傳「今天之後」最近一次會推播的日期跟當天要提醒的事項清單(可能
    不只一筆，同一天觸發多個事項時全部列出)。"""
    upcoming: list[tuple[date, dict]] = []
    for e in events:
        event_date = date.fromisoformat(e["date"])
        for trigger in thu_calendar_scraper.get_reminder_trigger_dates(event_date, e["title"]):
            if trigger > today:
                upcoming.append((trigger, e))
    if not upcoming:
        return None, []
    next_date = min(trigger for trigger, _ in upcoming)
    return next_date, [e for trigger, e in upcoming if trigger == next_date]


def _format_due_line(today: date, event_date: date, event: dict) -> str:
    date_str = event["date"] + (f"~{event['end_date']}" if event.get("end_date") else "")
    days_left = (event_date - today).days
    when = "就是今天" if days_left <= 0 else f"還有 {days_left} 天"
    return f"• {date_str}　{event['title']}（{when}）"


def _send_reminder(calendar: dict, today: date):
    due = _events_due_today(calendar["events"], today)
    if not due:
        return

    lines = [f"📅 *{calendar['semester']} 行事曆提醒*", ""]
    for event_date, event in sorted(due, key=lambda pair: pair[0]):
        lines.append(_format_due_line(today, event_date, event))

    next_date, next_events = _next_push(calendar["events"], today)
    if next_date:
        lines.append("")
        titles = "、".join(e["title"] for e in next_events)
        lines.append(f"🔜 下一次推播：{next_date.isoformat()}　{titles}")

    ok = notify_telegram.send_message(
        "\n".join(lines),
        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID,
    )
    if ok:
        logger.info("[thu_calendar] Telegram提醒推播成功，本次 %d 筆事項", len(due))
    else:
        logger.error("[thu_calendar] Telegram提醒推播失敗")


def run_thu_calendar():
    thu_calendar_scraper.invalidate_cache()  # 確保排除詞設定檔異動即時生效
    try:
        calendar = get_current_semester_calendar()
    except Exception as e:
        # 刻意不退回讀取昨天寫的data/thu_academic_calendar.json當作降級
        # 資料來源——http_client.get()本身已有3次重試，會走到這裡代表
        # Google Calendar這個大型服務當天真的整段掛掉，機率低到不值得
        # 為此多維護一套「用舊資料照樣推提醒」的分支與測試；後果最多是
        # 當天沒收到提醒，隔天資料若沒變照常補上，不是資料損毀。
        logger.error("[thu_calendar] 抓取/解析失敗，今天不推播提醒: %s", e)
        return

    with open(THU_CALENDAR_PATH, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
        f.write("\n")

    logger.info(
        "[thu_calendar] 已更新 %s（%s，共 %d 筆事件）",
        THU_CALENDAR_PATH, calendar["semester"], len(calendar["events"]),
    )

    _send_reminder(calendar, date.today())
