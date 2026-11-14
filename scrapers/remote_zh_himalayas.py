"""
scrapers/remote_zh_himalayas.py — Himalayas 遠端職缺中「需要中文」的職缺(2026-11-14新增)。

來源：himalayas.app 官方公開搜尋API(https://himalayas.app/jobs/api/search?q=...&page=N)，
無登入/驗證碼。實測(2026-11-14)：q=chinese/mandarin/traditional chinese 各5頁，
去重228筆，其中契約135/全職83/兼職9，幾乎沒有實習——所以這是獨立的
「中文母語遠端」頻道，不進實習頻道(會被實習關鍵字門檻濾光)。

地點過濾：locationRestrictions 為空(不限地點)，或包含 Taiwan/Germany/Worldwide/
Europe/Asia 才留(使用者在台灣、目前人在德國)；限特定其他國家的職缺全部丟掉。
"""
from __future__ import annotations

import time

from scrapers.http_client import request

SOURCE_ID = "remote_zh.himalayas"
SOURCE_NAME = "Himalayas 中文母語遠端"

API_URL = "https://himalayas.app/jobs/api/search"
QUERIES = ("chinese", "mandarin", "traditional chinese")
PAGES_PER_QUERY = 3
PAUSE_SECONDS = 1.0
OK_LOCATIONS = frozenset({"Taiwan", "Germany", "Worldwide", "Europe", "Asia"})
HEADERS = {"User-Agent": "Mozilla/5.0 (intel-pusher personal job monitor)"}


def location_ok(restrictions) -> bool:
    """不限地點，或可涵蓋台灣/德國的才算可接。"""
    if not restrictions:
        return True
    return any(loc in OK_LOCATIONS for loc in restrictions)


def _normalize(job: dict) -> dict | None:
    url = (job.get("applicationLink") or "").strip()
    title = (job.get("title") or "").strip()
    if not url or not title:
        return None
    company = (job.get("companyName") or "").strip()
    etype = job.get("employmentType") or ""
    where = ",".join(job.get("locationRestrictions") or []) or "不限地點"
    return {
        "title": f"{company} - {title}" if company else title,
        "summary": " | ".join(b for b in (etype, where) if b),
        "url": url,
        "published_at": None,
    }


def fetch() -> list[dict]:
    """回傳去重後、地點可接的職缺(正規化欄位)。"""
    seen: dict[str, dict] = {}
    for query in QUERIES:
        for page in range(1, PAGES_PER_QUERY + 1):
            resp = request("GET", API_URL, headers=HEADERS, params={"q": query, "page": page})
            jobs = resp.json().get("jobs") or []
            if not jobs:
                break
            for job in jobs:
                if not location_ok(job.get("locationRestrictions")):
                    continue
                item = _normalize(job)
                if item:
                    seen.setdefault(item["url"], item)
            time.sleep(PAUSE_SECONDS)
    return list(seen.values())


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    items = fetch()
    print(f"共 {len(items)} 筆")
    for it in items[:8]:
        print(f"  • {it['title']} | {it['summary']}")
