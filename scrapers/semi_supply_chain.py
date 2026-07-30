"""
scrapers/semi_supply_chain.py — 半導體供應鏈頻道(#semi-ai-supply-chain)。

範圍(使用者2026-07-30確認)：台積電的台灣上下游供應鏈廠商 + 海內外主要
客戶，不含台積電本身(已有獨立頻道，見scrapers/tsmc.py)。

兩個資料源合併：
1. scrapers/semi_tw_suppliers.py — 7家台灣上下游供應鏈廠商官方IR/新聞頁
   (3711日月光投控/2449京元電/3131弘塑/6187萬潤/3680家登/3583辛耘/
   1560中砂)，全部收錄，不需關鍵字過濾(官方IR頻道本身已是100%相關)。
2. scrapers/us_customer_feeds.py — 美股主要客戶(Apple/NVIDIA/AMD/
   Broadcom)官方新聞稿，這些feed九成以上是與晶片供應鏈無關的一般企業
   新聞，故用scrapers/us_customer_util.py關鍵字計分先篩過，只留跟晶片
   產能/代工/半導體/台灣生態系投資相關的項目(2026-07-30 B+E校準，見
   config/us_customer_keywords.json)。

不逐篇抓詳情頁全文摘要(7家台廠+4家美股客戶共11個來源，個別結構都
不同，全文抓取成本過高)，比照main.py DIGEST_CHANNELS的tsmc_digest/
cbc_digest模式，summary留給main.py的summarize_fn決定(這裡選擇直接
回傳None，只送標題給Gemini做交叉比對，多數公告標題本身已足夠說明)。
"""
from scrapers import semi_tw_suppliers, us_customer_feeds, us_customer_util

SOURCE_IDS = semi_tw_suppliers.SOURCE_IDS + [feed[0] for feed in us_customer_feeds.FEEDS]


def _fetch_us_customer_filtered() -> list[dict]:
    raw_items = us_customer_feeds.fetch_all()
    return [item for item in raw_items if us_customer_util.is_relevant(item["title"])]


def fetch() -> list[dict]:
    return semi_tw_suppliers.fetch_all() + _fetch_us_customer_filtered()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items = fetch()
    print(f"共 {len(items)} 篇（台廠+已過濾美股客戶）")
    for item in items:
        print(f"[{item['source_name']}] {item['title']}")
