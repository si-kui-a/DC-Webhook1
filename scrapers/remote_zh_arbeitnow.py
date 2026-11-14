"""
scrapers/remote_zh_arbeitnow.py — Arbeitnow(德國職缺)的實習職缺，提到中文者排前面(2026-11-14新增)。

來源：www.arbeitnow.com 官方公開 API(https://www.arbeitnow.com/api/job-board-api?page=N)，
免登入、無金鑰。實測(2026-11-14)：300筆中約21筆實習、沒有一筆提到中文，所以不能把
「中文」當必要條件(會整批濾光)，改成：
  - 篩選：標題或職缺類型含 Praktikum/Intern/Werkstudent 等實習字樣才留(使用者確認)。
  - 權重：標題提到中文 +2、內文提到中文 +1，回傳時依權重由高到低排，
    jobs/remote_zh.py 首次執行的筆數上限就會先留下中文職缺。
不用 source_hitrate.py 的命中率判斷：該篩選只排除不相符者，德國全職職缺幾乎全通過(99.4%)，
對此來源沒有鑑別力。
"""
from __future__ import annotations

import re
import time

from scrapers.http_client import request

SOURCE_ID = "remote_zh.arbeitnow"
SOURCE_NAME = "Arbeitnow 德國實習"

API_URL = "https://www.arbeitnow.com/api/job-board-api"
PAGES = 5
PAUSE_SECONDS = 1.0
HEADERS = {"User-Agent": "Mozilla/5.0 (intel-pusher personal job monitor)"}

# 用複合詞/字界，避免 intern 命中 international、internal
INTERN_RE = re.compile(r"praktik|\bintern(ship)?s?\b|werkstudent|working student|\bthesis\b|abschlussarbeit", re.I)
CHINESE_RE = re.compile(r"chinese|mandarin|chinesisch|中文|華語|华语|普通话|國語", re.I)


def is_intern(job: dict) -> bool:
    text = " ".join([job.get("title") or "", " ".join(job.get("job_types") or [])])
    return bool(INTERN_RE.search(text))


def chinese_weight(job: dict) -> int:
    """標題提到中文 2，內文/標籤提到 1，沒有 0。"""
    if CHINESE_RE.search(job.get("title") or ""):
        return 2
    body = " ".join([job.get("description") or "", " ".join(job.get("tags") or [])])
    return 1 if CHINESE_RE.search(body) else 0


def _normalize(job: dict) -> dict | None:
    url = (job.get("url") or "").strip()
    title = (job.get("title") or "").strip()
    if not url or not title:
        return None
    company = (job.get("company_name") or "").strip()
    place = "全遠端" if job.get("remote") else (job.get("location") or "").strip()
    tag = "中文 " if chinese_weight(job) else ""
    return {
        "title": f"{tag}{company} - {title}" if company else f"{tag}{title}",
        "summary": " | ".join(b for b in ("Arbeitnow", place) if b),
        "url": url,
        "published_at": None,
    }


def fetch() -> list[dict]:
    """回傳去重後的實習職缺，中文權重高的在前(同權重維持 API 順序)。"""
    seen: dict[str, tuple[int, dict]] = {}
    for page in range(1, PAGES + 1):
        resp = request("GET", API_URL, headers=HEADERS, params={"page": page})
        jobs = resp.json().get("data") or []
        if not jobs:
            break
        for job in jobs:
            if not is_intern(job):
                continue
            item = _normalize(job)
            if item:
                seen.setdefault(item["url"], (chinese_weight(job), item))
        time.sleep(PAUSE_SECONDS)
    return [item for _, item in sorted(seen.values(), key=lambda p: -p[0])]


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    items = fetch()
    print(f"共 {len(items)} 筆")
    for it in items[:8]:
        print(f"  • {it['title']} | {it['summary']}")
