"""
scrapers/thu_calendar.py — 東海大學官方行事曆(2026-09-10新增)。

官網行事曆頁(https://fsis.thu.edu.tw/wwwstud/info/Calendar.php)本身只是
外殼，實際內容用iframe內嵌Google Calendar公開行事曆(id:
qpejvbuas1qpq9ugasigipgvjs@group.calendar.google.com)——查看頁面原始碼
才發現，不是猜的。直接下載該行事曆的公開ics feed即可，不需要處理該HTML
外殼頁面或JS/iframe。

「當期學期」邊界用行事曆裡明確標記的「N學年度第M學期開始」事件決定，不用
自行假設8/1、2/1固定日期——實測每年真正開學日略有出入(2026-09-10抓到的
ics裡115學年度第1學期開始=2026-08-01、第2學期開始=2027-02-01)，用資料
自己的標記比寫死日期可靠。

實測結果(2026-09-10，1687筆VEVENT)：SUMMARY欄位出現次數跟BEGIN:VEVENT
完全1:1，代表SUMMARY沒有被RFC5545行折疊(line folding)過，因此不需要處理
折疊邏輯；DTEND每筆事件都存在。ICS TEXT逸出字元(\\, \\; \\n)只出現在
DESCRIPTION，SUMMARY目前沒有，但仍做防禦性unescape以免未來新增事項帶
逸出字元時解析出錯。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from scrapers.http_client import get

logger = logging.getLogger("scrapers.thu_calendar")

SOURCE_NAME = "東海大學行事曆"
SOURCE_ID = "thu_calendar"

ICS_URL = (
    "https://calendar.google.com/calendar/ical/"
    "qpejvbuas1qpq9ugasigipgvjs%40group.calendar.google.com/public/basic.ics"
)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_EXCLUDE_PATH = _CONFIG_DIR / "thu_calendar_exclude.json"

_SEMESTER_START_RE = re.compile(r"^(\d+)\s*學年度第\s*([12])\s*學期開始$")
_SEMESTER_BOUNDARY_RE = re.compile(r"^\d+\s*學年度第\s*[12]\s*學期(開始|終了)$")
_DTSTART_RE = re.compile(r"^DTSTART(?:;[^:\n]*)?:(\d{8})", re.MULTILINE)
_DTEND_RE = re.compile(r"^DTEND(?:;[^:\n]*)?:(\d{8})", re.MULTILINE)
_SUMMARY_RE = re.compile(r"^SUMMARY:(.*)$", re.MULTILINE)

_exclude_keywords_cache: list[str] | None = None


def _load_exclude_keywords() -> list[str]:
    """讀config/thu_calendar_exclude.json，攤平成單一關鍵字清單(子字串
    比對，比照scholarship_util.py/internship_util.py既有的排除詞慣例)。"""
    global _exclude_keywords_cache
    if _exclude_keywords_cache is not None:
        return _exclude_keywords_cache
    try:
        with open(_EXCLUDE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("讀取排除詞設定失敗 %s: %s，本次不排除任何事件", _EXCLUDE_PATH, e)
        _exclude_keywords_cache = []
        return _exclude_keywords_cache

    keywords = []
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        keywords.extend(value)
    _exclude_keywords_cache = keywords
    return keywords


def invalidate_cache():
    """外部(main.py)在每次執行前呼叫，確保排除詞設定變更即時生效。"""
    global _exclude_keywords_cache
    _exclude_keywords_cache = None


def is_relevant_to_students(title: str) -> bool:
    """排除明確屬於教職員行政會議/教職員專屬活動/全校設施維護/校友或公開
    慶典類的事項(使用者2026-09-10確認「只要推播與在學學生明確相關的」)；
    其餘一律視為相關，不做未經確認的臆測式二次篩選。"""
    return not any(kw in title for kw in _load_exclude_keywords())


def _unescape_ics_text(value: str) -> str:
    """ICS TEXT escaping(RFC 5545 §3.3.11)。"""
    return (
        value.replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\\\", "\\")
    ).strip()


def _parse_ics_date(value: str) -> date:
    return datetime.strptime(value[:8], "%Y%m%d").date()


def fetch_raw_events() -> list[dict]:
    """下載並解析整份ics，回傳全部事件(未依學期篩選)。all-day事件的
    DTEND依ICS規範是「排他」結束日(隔天)，這裡先轉成「實際最後一天」
    (DTEND-1)，跟一般人認知的日期一致。"""
    resp = get(ICS_URL)
    resp.raise_for_status()
    text = resp.text

    events = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        start_m = _DTSTART_RE.search(block)
        summary_m = _SUMMARY_RE.search(block)
        if not start_m or not summary_m:
            continue
        start = _parse_ics_date(start_m.group(1))

        end_m = _DTEND_RE.search(block)
        end = _parse_ics_date(end_m.group(1)) - timedelta(days=1) if end_m else start
        if end < start:
            end = start  # 防禦：理論上不會發生，DTEND本該晚於DTSTART

        events.append({
            "start": start,
            "end": end,
            "title": _unescape_ics_text(summary_m.group(1)),
        })
    return events


def get_current_semester_calendar(today: date | None = None) -> dict:
    """回傳當期學期(依「今天」判定)的行事曆事件清單。"""
    today = today or date.today()
    events = fetch_raw_events()

    markers = []
    for e in events:
        m = _SEMESTER_START_RE.match(e["title"])
        if m:
            markers.append((e["start"], f"{m.group(1)}學年度第{m.group(2)}學期"))
    markers.sort()
    if not markers:
        raise ValueError("行事曆裡找不到任何「N學年度第M學期開始」標記，無法判斷當期學期範圍")

    current_start, current_label, next_start = None, None, None
    for start, label in markers:
        if start <= today:
            current_start, current_label = start, label
        elif current_start is not None:
            next_start = start
            break

    if current_start is None:
        # 今天早於資料集裡最早的學期標記，理論上不會發生(行事曆回溯到民國初年)
        current_start, current_label = markers[0]

    if next_start is None:
        next_start = current_start + timedelta(days=180)

    selected = sorted(
        (
            e for e in events
            if current_start <= e["start"] < next_start
            # 學期起訖標記本身已經反映在semester/range_start/range_end
            # 欄位，events清單裡不重複列出。
            and not _SEMESTER_BOUNDARY_RE.match(e["title"])
            and is_relevant_to_students(e["title"])
        ),
        key=lambda e: e["start"],
    )

    return {
        "semester": current_label,
        "range_start": current_start.isoformat(),
        "range_end": next_start.isoformat(),
        "events": [
            {
                "date": e["start"].isoformat(),
                "end_date": e["end"].isoformat() if e["end"] != e["start"] else None,
                "title": e["title"],
            }
            for e in selected
        ],
    }
