"""
scrapers/cbc.py — 台灣中央銀行新聞稿。

已驗證來源（直接 curl 確認，非搜尋推測）：
- /tw/lp-302-1.html 這個 HTML 頁面本身只有導覽選單，curl 實測拿不到任何新聞稿清單
  （不是 selector 寫錯，是這頁本來就沒有清單內容）。
- 官方有提供 RSS：https://www.cbc.gov.tw/tw/rss-302-1.xml，curl 實測內容完整、
  標準 <item><title><link><pubDate> 結構，比硬解析 HTML 可靠，改用這個。

注意：這份 RSS 沒有日期篩選，一次拿回整個歷史（curl 實測 500 筆）。fetch()
本身不做截斷——全部 500 筆都會回傳，讓 db 建立完整的 dedup_key 歷史記錄。
「資料庫是空的時候不要把 500 筆當新項目全部推播」這件事改在 main.py 的
「首次執行安全閘門」處理（只有 cbc 套用這個閘門，見 main.py 的
FIRST_RUN_PUSH_CAP），而不是在這裡直接丟棄舊資料，這樣未來如果閘門邏輯
調整，舊歷史資料仍然完整在 db 裡，不需要重新回補。
"""
import xml.etree.ElementTree as ET

import requests

SOURCE_ID = "cbc.press_releases"
SOURCE_NAME = "台灣中央銀行-新聞稿"
BASE_URL = "https://www.cbc.gov.tw/tw/rss-302-1.xml"

HEADERS = {
    "User-Agent": "IntelPusher/0.1 (personal research bot; contact: your-email@example.com)"
}


def fetch() -> list[dict]:
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    results = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        results.append({
            "title": (item.findtext("title") or "").strip(),
            "summary": None,
            # RSS description 是原始 HTML（含 <p>/<table> 等標籤），交給
            # summarizer_zh.summarize() 自己清洗+摘要，這裡不先處理，
            # 保持 fetch() 只負責「抓資料」，不做摘要邏輯。
            "raw_description": item.findtext("description") or "",
            "url": link,
            "published_at": (item.findtext("pubDate") or "").strip(),
        })
    return results
