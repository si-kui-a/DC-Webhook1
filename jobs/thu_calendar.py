"""jobs/thu_calendar.py — 東海大學當期學期行事曆合併到倉庫(2026-09-10新增)。

取代原本教育類(scholarship/internship)的Telegram通知(使用者2026-09-10
確認拿掉)：這支job不推播，只把scrapers/thu_calendar.py抓到的當期學期
事件寫成data/thu_academic_calendar.json，交給backup.sh既有的每日自動
git commit+push機制一併帶進版控(見backup.sh的git add清單)。內容不含
產生時間戳，只有事件本身變動時才會讓git偵測到差異，避免每天固定跑出
無意義的auto backup commit。
"""
import json
import logging

from jobs.paths import THU_CALENDAR_PATH
from scrapers import thu_calendar as thu_calendar_scraper
from scrapers.thu_calendar import get_current_semester_calendar

logger = logging.getLogger("main")


def run_thu_calendar():
    thu_calendar_scraper.invalidate_cache()  # 確保排除詞設定檔異動即時生效
    try:
        calendar = get_current_semester_calendar()
    except Exception as e:
        logger.error("[thu_calendar] 抓取/解析失敗: %s", e)
        return

    with open(THU_CALENDAR_PATH, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
        f.write("\n")

    logger.info(
        "[thu_calendar] 已更新 %s（%s，共 %d 筆事件）",
        THU_CALENDAR_PATH, calendar["semester"], len(calendar["events"]),
    )
