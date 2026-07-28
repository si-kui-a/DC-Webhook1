"""
scrapers/scholarship_daad.py — DAAD 德國獎學金資料庫。

DAAD 的獎學金清單頁是純前端渲染，實際資料來自一份公開靜態 JS 資料檔
（https://www2.daad.de/.../scholarships.js），內容格式為
`var scholarships = TAFFY([...])`，剝掉外層後即為 JSON 陣列。
"""
import json
import logging
import re

import requests

logger = logging.getLogger("scrapers.scholarship_daad")

SOURCE_ID = "scholarship.daad"
SOURCE_NAME = "DAAD 德國獎學金資料庫"

DATA_URL = (
    "https://www2.daad.de/bundles/daadstipendiendatenbanklsh/data/a/js/scholarships.js"
)
DETAIL_BASE_URL = (
    "https://www.daad.de/deutschland/stipendium/datenbank/en/"
    "21148-scholarship-database/"
)

# DAAD 伺服器會擋帶有自訂 User-Agent 的請求（403），
# 但完全不帶 User-Agent 反而正常回傳資料（Node.js 的 axios 預設行為即如此）。
# 所以這裡不設 HEADERS，直接發送不帶 User-Agent 的請求。


def fetch() -> list[dict]:
    resp = requests.get(DATA_URL, timeout=15)
    resp.raise_for_status()

    raw = resp.text.strip()
    # 剝掉 var scholarships = TAFFY( ... );
    json_text = re.sub(r"^var\s+scholarships\s*=\s*TAFFY\(", "", raw)
    json_text = re.sub(r"\);?\s*$", "", json_text)

    try:
        records = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error("DAAD JSON 解析失敗: %s", e)
        return []

    results = []
    for r in records:
        title = (
            r.get("programmnameEn")
            or r.get("nameEn")
            or r.get("programmnameDe")
            or r.get("nameDe")
        )
        sap_id = r.get("sapProgid")
        if not title or not sap_id:
            continue
        results.append({
            "title": title.strip(),
            "summary": None,
            "url": f"{DETAIL_BASE_URL}?detail={sap_id}&lang=en",
            "published_at": None,
        })

    logger.info("DAAD 抓取完成，共 %d 筆", len(results))
    return results


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for item in fetch():
        print(f"  • {item['title']}")
        print(f"    {item['url']}")
