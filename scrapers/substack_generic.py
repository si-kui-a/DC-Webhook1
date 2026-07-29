"""
scrapers/substack_generic.py — 通用 Substack RSS 抓取器。

這些來源全部都是 Substack,RSS結構完全相同,只有網域/主題不同,故共用
一份程式碼(設定檔驅動),不為每個來源各開一支scraper檔案(見設計討論
的「簡化/維護性」考量)——之後新增來源只要在FEEDS加一筆設定。

免費預覽萃取邏輯(沿用原本scrapers/substack_easypoint.py的做法):
1. content:encoded的HTML去標籤取純文字
2. 在「Read more」處截斷(付費牆標準CTA文字)
3. 移除Substack自動插入的訂閱推廣樣板文字
4. 不做額外長度上限截斷——抓到哪裡算哪裡,碰到付費牆就停止擷取
   (原本400字上限的做法已移除,見設計討論)
"""
import re
import xml.etree.ElementTree as ET
from html import unescape

import requests

_CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"

_SUBSTACK_BOILERPLATE_PATTERNS = [
    r"This Substack is reader-supported\. To receive new posts.*?free or paid subscriber\.",
    r"Thanks for reading!?\s*Subscribe for free to receive new posts and support my work\.",
]

HEADERS = {
    "User-Agent": "IntelPusher/0.2 (personal research bot; contact: your-email@example.com)"
}

# (source_id, source_name, feed_url) 設定清單——美股個股/技術分析統整頻道
# 用的來源(見設計討論的頻道分組)。openbookandeasypoint原本在獨立的
# substack_easypoint.py,現在統一走這支通用scraper,獨立檔案保留給程式碼
# 沿用(main.py暫時兩邊並存,digest pipeline改用這裡的設定)。
US_STOCK_FEEDS = [
    ("substack.openbookandeasypoint", "美股送分題(Substack)", "https://openbookandeasypoint.substack.com/feed"),
    ("substack.unclestocknotes", "Substack大叔", "https://unclestocknotes.substack.com/feed"),
    ("substack.thesetupfactory", "The Setup Factory", "https://thesetupfactory.substack.com/feed"),
    ("substack.mimivsjames2", "MimiVsJames美股策略分析", "https://mimivsjames2.substack.com/feed"),
    ("substack.90spminvesting", "90s.pm.investing", "https://90spminvesting.substack.com/feed"),
]

# 加密貨幣統整。princetonchen、wublockchain123跨頻(也出現在總經科技趨勢統整)——
# 跨頻來源用「同一個feed網址+不同source_id」重複設定,確保每個頻道各自獨立
# 去重(見設計討論:同一篇文章要能同時被不同頻道各自的AI用不同切入角度
# 消化,若共用同一個source_id,先處理的頻道會把dedup_key標記掉,導致其他
# 頻道永遠抓不到這篇——這是實測前就先設計避開的已知陷阱)。
CRYPTO_FEEDS = [
    ("substack.cryptowesearch", "幣研週報", "https://cryptowesearch.substack.com/feed"),
    ("substack.wublockchain123.crypto", "吳說區塊鏈", "https://wublockchain123.substack.com/feed"),
    ("substack.maxcrypto", "Max的加密貨幣研究", "https://www.maxcrypto.space/feed"),
    ("substack.princetonchen.crypto", "Tiger Capital Research", "https://princetonchen.substack.com/feed"),
]

# 總經/科技趨勢統整。wublockchain123跨頻。
MACRO_TECH_FEEDS = [
    ("substack.mviewpoint", "M報", "https://mviewpoint.substack.com/feed"),
    ("substack.yasac", "股癌自動筆記", "https://yasac.substack.com/feed"),
    ("substack.wublockchain123.macrotech", "吳說區塊鏈", "https://wublockchain123.substack.com/feed"),
    ("substack.princetonchen.macrotech", "Tiger Capital Research", "https://princetonchen.substack.com/feed"),
]

# 地緣政治/安全/時事統整。princetonchen跨頻。
# winginvest原本規劃跨頻放這裡,但其feed網址一直無法驗證(/feed與/archive
# 都被導回個人檔案頁,非公開的Posts列表),使用者已確認放棄這個來源。
GEOPOLITICS_FEEDS = [
    ("substack.princetonchen.geo", "Tiger Capital Research", "https://princetonchen.substack.com/feed"),
]


def _extract_free_preview(raw_html: str) -> str:
    """從content:encoded的原始HTML萃取免費預覽純文字(Read more之前的部分),
    清掉Substack自動插入的訂閱推廣樣板。不做長度上限截斷。"""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = unescape(text)

    cutoff = text.find("Read more")
    if cutoff > 0:
        text = text[:cutoff]

    for pattern in _SUBSTACK_BOILERPLATE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.DOTALL)

    return re.sub(r"\s+", " ", text).strip()


def fetch_feed(source_id: str, source_name: str, feed_url: str) -> list[dict]:
    """抓單一Substack feed,回傳每篇文章的標題+免費預覽+連結。單篇解析
    失敗不中斷其他項目(比照既有scraper的容錯原則)。"""
    resp = requests.get(feed_url, headers=HEADERS, timeout=15)
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
            "summary": preview or "（無可用預覽，可能為會員專屬內容）",
            "url": link,
            "published_at": (item.findtext("pubDate") or "").strip(),
            "source_id": source_id,
            "source_name": source_name,
            "telegram_alert": False,
        })
    return results


def fetch_all(configs: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """抓取多個feed,單一來源失敗不中斷其他來源(比照既有scraper容錯原則)。"""
    configs = configs if configs is not None else US_STOCK_FEEDS
    all_items = []
    for source_id, source_name, feed_url in configs:
        try:
            all_items.extend(fetch_feed(source_id, source_name, feed_url))
        except Exception as e:
            all_items.append({
                "title": f"{source_name} - 抓取失敗",
                "summary": f"無法取得資料：{e}",
                "url": feed_url,
                "published_at": None,
                "source_id": source_id,
                "source_name": source_name,
                "telegram_alert": False,
            })
    return all_items


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items = fetch_all()
    print(f"共 {len(items)} 篇（跨 {len(US_STOCK_FEEDS)} 個來源）")
    for item in items[:3]:
        print(f"\n=== [{item['source_name']}] {item['title']} ===")
        print(f"summary: {item['summary'][:200]}")
