"""jobs/thu_events.py — 已報名的東海校園活動提醒(2026-10-07新增)。

活動資料來自「東海大學-校園活動報名系統」的報名成功信：scripts/add_thu_event.py
把信件內容解析成活動資料(只留活動名稱、日期、時間、地點、報到、福利、報名狀態與
連結；姓名、電話、E-Mail一律不存)，存在本機 private/thu_events.json(不進版控)，
並同步到 GitHub Secret THU_EVENTS——這個 repo 是公開的，個人行程不能 commit。

每小時由雲端排程跑一次(scripts/cloud_scheduler.py 的 EVERY_TICK)，用 EDU bot 推播。
提醒點：活動開始前一週、前一天、前三小時、前二小時、前一小時(使用者2026-10-07指定)。
排程每小時一次、分鐘數不固定，所以「到了提醒點之後的第一次執行」才送，訊息裡寫實際
剩餘時間；已送的提醒記在 work/thu_event_reminders.json(隨加密狀態保存)，不重送。
同一活動同時有好幾個提醒點已到(排程延遲)時只送最近的一個，舊的直接記為已送。
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "private" / "thu_events.json"
STATE_PATH = ROOT / "work" / "thu_event_reminders.json"
TAIWAN_TZ = timezone(timedelta(hours=8))
REMINDERS = (("7d", timedelta(days=7)), ("1d", timedelta(days=1)),
             ("3h", timedelta(hours=3)), ("2h", timedelta(hours=2)), ("1h", timedelta(hours=1)))
_WEEKDAY = "一二三四五六日"
# 備註裡跟參加者有關的好處；其他注意事項(報名規則、聯絡人)不推
_BENEFIT_RE = re.compile(r"餐|餐盒|點心|博雅|認證|時數|證書|獎勵|贈")


def _field(text: str, name: str) -> str:
    m = re.search(rf"^\s*{name}\s*[：:]\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


def parse_registration(text: str) -> dict:
    """報名成功信 -> 活動資料。缺必要欄位時 raise ValueError，不存半套資料。"""
    name = _field(text, "活動名稱")
    dates = re.split(r"\s*[～~]\s*", _field(text, "活動日期"))
    times = re.split(r"\s*[～~]\s*", _field(text, "活動時間"))
    if not name or len(dates) != 2 or len(times) != 2:
        raise ValueError("找不到活動名稱、活動日期或活動時間；請貼完整的報名成功信")
    link = re.search(r"https?://event\.ithu\.tw/(\d+)", text)
    notes = text.split("以下為您所報名的場次")[0].split("備註")[-1]
    benefits = []
    for tag in re.findall(r"【([^】]+)】", name):
        benefits.append(tag)
    for line in notes.splitlines():
        line = line.strip().strip("。")
        if line and _BENEFIT_RE.search(line) and line not in benefits and "報名" not in line:
            benefits.append(line)
    checkin_note = re.search(r"於\s*([\d:]+)\s*至\s*([\d:]+)\s*準時", notes)
    return {
        "id": link.group(1) if link else f"{dates[0]}-{name}",
        "name": name,
        "place": _field(text, "活動地點"),
        "start": f"{dates[0]}T{times[0]}",
        "end": f"{dates[1]}T{times[1]}",
        "checkin": _field(text, "報到時間").replace("～", "–").replace("~", "–"),
        "checkin_note": f"{checkin_note.group(1)}–{checkin_note.group(2)}" if checkin_note else "",
        "benefits": benefits,
        "status": "已報名",
        "url": link.group(0).replace("http://", "https://") if link else "",
    }


def load_events() -> list[dict]:
    """雲端從 THU_EVENTS(Secret)讀，本機從 private/thu_events.json 讀。"""
    raw = os.environ.get("THU_EVENTS", "").strip()
    if not raw and EVENTS_PATH.exists():
        raw = EVENTS_PATH.read_text(encoding="utf-8")
    return json.loads(raw) if raw else []


def _start(event: dict) -> datetime:
    return datetime.fromisoformat(event["start"]).replace(tzinfo=TAIWAN_TZ)


def _remaining(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes >= 24 * 60:
        return f"還有 {round(minutes / 1440)} 天"
    hours, mins = divmod(minutes, 60)
    if not hours:
        return f"還有 {mins} 分"
    return f"還有 {hours} 小時" + (f" {mins} 分" if mins else "")


def format_event(event: dict, now: datetime) -> str:
    start = _start(event)
    end = datetime.fromisoformat(event["end"]).replace(tzinfo=TAIWAN_TZ)
    day = f"{start:%Y-%m-%d}（{_WEEKDAY[start.weekday()]}）"
    if end.date() != start.date():
        day += f"～{end:%Y-%m-%d}"
    checkin = event.get("checkin", "")
    if event.get("checkin_note") and event["checkin_note"] not in checkin:
        checkin = f"{checkin}（請於 {event['checkin_note']} 準時報到）".strip()
    lines = [f"⏰ <b>活動提醒｜{_remaining(start - now)}</b>",
             f"<b>{html.escape(event['name'])}</b>",
             f"🗓 {day} {start:%H:%M}–{end:%H:%M}",
             f"📍 {html.escape(event.get('place', ''))}"]
    if checkin:
        lines.append(f"📝 報到 {html.escape(checkin)}")
    if event.get("benefits"):
        lines.append(f"🎁 {html.escape('；'.join(event['benefits']))}")
    lines.append(f"✅ {event.get('status', '已報名')}" + (f"｜{event['url']}" if event.get("url") else ""))
    return "\n".join(lines)


def due(events: list[dict], sent: dict, now: datetime) -> tuple[list[dict], dict]:
    """(這次要送的活動, 更新後的已送紀錄)。只看還沒開始的活動；已開始的活動從紀錄裡移除。"""
    to_send, new_sent = [], {}
    for event in events:
        start = _start(event)
        if now >= start:
            continue
        done = set(sent.get(event["id"], []))
        reached = [label for label, offset in REMINDERS if start - offset <= now]
        if reached and reached[-1] not in done:
            to_send.append(event)
        new_sent[event["id"]] = sorted(done | set(reached))
    return to_send, new_sent


def run_thu_events(now: datetime | None = None) -> bool:
    import notify_telegram

    now = now or datetime.now(TAIWAN_TZ)
    try:
        events = load_events()
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[thu_events] 活動資料讀取失敗: %s", e)
        return False
    sent = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    to_send, new_sent = due(events, sent, now)
    if to_send:
        message = "\n\n".join(format_event(e, now) for e in sorted(to_send, key=_start))
        ok = notify_telegram.send_message(message, parse_mode="HTML",
                                          bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
                                          chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID)
        if not ok:
            logger.error("[thu_events] Telegram推播失敗，下次執行重送")
            return False  # 不記為已送
        logger.info("[thu_events] 推播 %d 個活動提醒", len(to_send))
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(new_sent, ensure_ascii=False, indent=1), encoding="utf-8")
    return True
