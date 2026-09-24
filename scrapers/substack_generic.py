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
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from html import unescape

import requests
from scrapers import http_client

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


RSS2JSON_ENDPOINT = "https://api.rss2json.com/v1/api.json"


def _is_cloudflare_challenge(exc: requests.HTTPError) -> bool:
    resp = exc.response
    return resp is not None and resp.status_code == 403 and resp.headers.get("cf-mitigated") == "challenge"


def _raw_entries(feed_url: str) -> list[tuple[str, str, str, str]]:
    """回傳(link, title, content_html, pubDate RFC822)。

    Substack的Cloudflare會對雲端機房IP(GitHub Actions runner)直接回403
    challenge，換UA/Accept/curl/curl_cffi都一樣被擋(2026-09-24在runner上
    實測)；只有這種情況才改走rss2json(免key、每日1萬次、每feed最多10篇、
    約1小時快取)。本機住宅IP照舊直抓，行為不變。rss2json的content欄位比
    content:encoded短，但_extract_free_preview()切出來的結果實測逐篇相同。"""
    try:
        resp = http_client.get(feed_url, headers=HEADERS, timeout=15)
    except requests.HTTPError as e:
        if not _is_cloudflare_challenge(e):
            raise
        return _raw_entries_via_rss2json(feed_url)
    root = ET.fromstring(resp.content)
    return [
        ((item.findtext("link") or "").strip(), (item.findtext("title") or "").strip(),
         item.findtext(_CONTENT_ENCODED_TAG) or "", (item.findtext("pubDate") or "").strip())
        for item in root.findall(".//item")
    ]


def _raw_entries_via_rss2json(feed_url: str) -> list[tuple[str, str, str, str]]:
    # 不帶key時rss2json約連續10次就回429且數分鐘不解除(2026-09-24實測)，
    # 20:00那輪4個digest共要打約11次，所以雲端要設RSS2JSON_API_KEY(免費帳號)；
    # 帶key才能用count，順便拉到跟原feed一樣的20篇。
    params = {"rss_url": feed_url}
    api_key = os.getenv("RSS2JSON_API_KEY")
    if api_key:
        params.update(api_key=api_key, count="20")
    resp = http_client.get(RSS2JSON_ENDPOINT, params=params, timeout=30)
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"rss2json: {data.get('message') or data.get('status')}")
    entries = []
    for item in data.get("items", []):
        # rss2json把pubDate轉成"YYYY-MM-DD HH:MM:SS"(UTC)，轉回原feed的RFC822格式
        # 讓下游parsedate_to_datetime照常運作。
        try:
            pub = datetime.strptime(item.get("pubDate", ""), "%Y-%m-%d %H:%M:%S").strftime("%a, %d %b %Y %H:%M:%S GMT")
        except ValueError:
            pub = ""
        entries.append(((item.get("link") or "").strip(), (item.get("title") or "").strip(),
                        item.get("content") or "", pub))
    return entries


def fetch_feed(source_id: str, source_name: str, feed_url: str) -> list[dict]:
    """抓單一Substack feed,回傳每篇文章的標題+免費預覽+連結。單篇解析
    失敗不中斷其他項目(比照既有scraper的容錯原則)。"""
    results = []
    for link, title, raw_html, published_at in _raw_entries(feed_url):
        if not link or not title:
            continue

        try:
            preview = _extract_free_preview(raw_html) if raw_html else ""
        except Exception:
            preview = ""

        results.append({
            "title": title,
            "summary": preview or "（無可用預覽，可能為會員專屬內容）",
            "url": link,
            "published_at": published_at,
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
