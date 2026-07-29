"""
scrapers/substack_easypoint.py — 《美股送分題》(openbookandeasypoint) Substack
每日新文章通知。只推免費預覽,不碰付費牆。

已驗證(直接curl+分析RSS內容,非搜尋推測):
- https://openbookandeasypoint.substack.com/feed 是真實可用的RSS,標準
  <item><title><link><pubDate><content:encoded>結構。
- 該站台已於2026-07-06左右轉為付費訂閱制(feed裡有一篇文章標題就是
  《一封信：為什麼《美股送分題》決定開始收費？》)。實測20篇文章的
  content:encoded長度,7/6之後發的文章多數在1,000～10,000字左右被截斷
  成「Read more」連回付費頁面——RSS只給得到免費預覽段落,不是完整分析。
  使用者已確認:不訂閱、只要免費預覽+連結即可,不追求完整內容。
- 只推Discord,不推Telegram(main.py讀取telegram_alert=False決定)。

免費預覽萃取邏輯:
1. content:encoded的HTML去標籤取純文字
2. 在「Read more」處截斷(該站台付費牆的標準CTA文字)
3. 移除Substack自動插入的訂閱推廣文字(非作者本人寫的內容,已知的固定
   樣板,見_SUBSTACK_BOILERPLATE_PATTERNS——比照summarizer_zh.py既有的
   「已知樣板regex清洗,不修改第三方套件行為」原則,這裡沒有第三方套件
   可言,但同樣是「已知固定樣板,精確清除」的做法)
4. 截斷至PREVIEW_MAX_LENGTH,避免Discord embed description超過4096字元上限
   (實務上不會真的抓到那麼長,但仍設一個保守上限)
"""
import re
import xml.etree.ElementTree as ET
from html import unescape

import requests

SOURCE_ID = "substack.openbookandeasypoint"
SOURCE_NAME = "美股送分題(Substack)"
FEED_URL = "https://openbookandeasypoint.substack.com/feed"
PREVIEW_MAX_LENGTH = 400

_CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"

_SUBSTACK_BOILERPLATE_PATTERNS = [
    r"This Substack is reader-supported\. To receive new posts.*?free or paid subscriber\.",
    r"Thanks for reading!\s*Subscribe for free to receive new posts and support my work\.",
]

HEADERS = {
    "User-Agent": "IntelPusher/0.2 (personal research bot; contact: your-email@example.com)"
}


def _extract_free_preview(raw_html: str) -> str:
    """從content:encoded的原始HTML萃取免費預覽純文字(Read more之前的部分),
    清掉Substack自動插入的訂閱推廣樣板,截斷至合理長度。"""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = unescape(text)

    cutoff = text.find("Read more")
    if cutoff > 0:
        text = text[:cutoff]

    for pattern in _SUBSTACK_BOILERPLATE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.DOTALL)

    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > PREVIEW_MAX_LENGTH:
        truncated = text[:PREVIEW_MAX_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        text = truncated + "..."
    return text


def fetch() -> list[dict]:
    """抓取RSS,回傳每篇文章的標題+免費預覽+連結。單篇解析失敗不中斷其他
    項目(比照既有scraper的容錯原則)。"""
    resp = requests.get(FEED_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    results = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue

        raw_html = item.findtext(_CONTENT_ENCODED_TAG) or ""
        try:
            preview = _extract_free_preview(raw_html) if raw_html else ""
        except Exception:
            preview = ""

        results.append({
            "title": title,
            "summary": preview or "（無可用預覽，請點擊標題查看原文——可能為會員專屬內容）",
            "url": link,
            "published_at": (item.findtext("pubDate") or "").strip(),
            "telegram_alert": False,  # 只推Discord,不推Telegram(使用者確認)
        })
    return results


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items = fetch()
    print(f"共 {len(items)} 篇")
    for item in items[:3]:
        print(f"\n=== {item['title']} ===")
        print(f"published_at: {item['published_at']}")
        print(f"summary: {item['summary']}")
        print(f"url: {item['url']}")
