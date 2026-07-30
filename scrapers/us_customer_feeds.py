"""
scrapers/us_customer_feeds.py — 台積電美股主要客戶新聞稿(免key,官方RSS/Atom)。

2026-07-30查證台積電前七大客戶(蘋果25-27%/輝達11%/聯發科9%/高通8%/
超微7%/博通7%/英特爾6%)，扣除已是台灣上市的聯發科(見semi_supply_chain.py)，
逐一實測其餘6家的官方新聞稿feed：
- Apple/NVIDIA/AMD/Broadcom：已直接curl驗證可用(見下方FEEDS)
- Qualcomm：兩個候選網址皆404/403，暫不收錄
- Intel：newsroom.intel.com/feed回傳403(疑似bot防護)，暫不收錄
使用者確認2026-07-30：先做已驗證的4家，Qualcomm/Intel之後有空再研究。

Apple走Atom格式(<feed><entry>)，其餘3家為標準RSS 2.0(<channel><item>)，
共用一支通用scraper(比照scrapers/substack_generic.py的設定檔驅動模式)。
"""
import xml.etree.ElementTree as ET

import requests

ATOM_NS = "{http://www.w3.org/2005/Atom}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# (source_id, source_name, feed_url, is_atom)
FEEDS = [
    ("us_customer.apple", "Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss", True),
    ("us_customer.nvidia", "NVIDIA Newsroom", "https://nvidianews.nvidia.com/rss.xml", False),
    ("us_customer.amd", "AMD Press Releases", "https://ir.amd.com/news-events/press-releases/rss", False),
    ("us_customer.broadcom", "Broadcom News Releases", "https://investors.broadcom.com/rss/news-releases.xml", False),
]


def _parse_atom(root, source_id: str, source_name: str) -> list[dict]:
    results = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title_el = entry.find(f"{ATOM_NS}title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        # entry可能有多個link(文章本身+rel="enclosure"的圖片)，取非enclosure的那個。
        url = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            if link_el.get("rel") != "enclosure":
                url = link_el.get("href", "")
                break
        if not title or not url:
            continue

        updated_el = entry.find(f"{ATOM_NS}updated")
        content_el = entry.find(f"{ATOM_NS}content")
        results.append({
            "title": title,
            "summary": (content_el.text or "").strip() if content_el is not None else None,
            "url": url,
            "published_at": (updated_el.text or "").strip() if updated_el is not None else None,
            "source_id": source_id,
            "source_name": source_name,
            "telegram_alert": False,
        })
    return results


def _parse_rss(root, source_id: str, source_name: str) -> list[dict]:
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        results.append({
            "title": title,
            "summary": (item.findtext("description") or "").strip() or None,
            "url": url,
            "published_at": (item.findtext("pubDate") or "").strip() or None,
            "source_id": source_id,
            "source_name": source_name,
            "telegram_alert": False,
        })
    return results


def fetch_feed(source_id: str, source_name: str, feed_url: str, is_atom: bool) -> list[dict]:
    resp = requests.get(feed_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return _parse_atom(root, source_id, source_name) if is_atom else _parse_rss(root, source_id, source_name)


def fetch_all() -> list[dict]:
    """抓取多個feed,單一來源失敗不中斷其他來源(比照既有scraper容錯原則)。"""
    all_items = []
    for source_id, source_name, feed_url, is_atom in FEEDS:
        try:
            all_items.extend(fetch_feed(source_id, source_name, feed_url, is_atom))
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
    print(f"共 {len(items)} 篇（跨 {len(FEEDS)} 個來源）")
    for item in items[:8]:
        print(f"\n=== [{item['source_name']}] {item['title']} ===")
        print(f"published_at: {item['published_at']}")
        print(f"url: {item['url']}")
