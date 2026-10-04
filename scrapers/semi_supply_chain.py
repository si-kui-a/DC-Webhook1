"""
scrapers/semi_supply_chain.py — 半導體供應鏈頻道(#semi-ai-supply-chain)。

範圍(使用者2026-07-30確認)：台積電的台灣上下游供應鏈廠商，不含台積電
本身(已有獨立頻道，見scrapers/tsmc.py)。

資料源：scrapers/semi_tw_suppliers.py — 7家台灣上下游供應鏈廠商官方IR/
新聞頁(3711日月光投控/2449京元電/3131弘塑/6187萬潤/3680家登/3583辛耘/
1560中砂)，全部收錄，不需關鍵字過濾(官方IR頻道本身已是100%相關)。

2026-10-04移除美股客戶(Apple/NVIDIA/AMD/Broadcom)新聞稿：關鍵字篩選
2026-07-30之後一則都沒通過(10-04實測51則全部0分)，每天白打4個請求，
比照518的命中率0%移除標準。原檔案見git歷史(us_customer_feeds.py/
us_customer_util.py/config/us_customer_keywords.json)。

不逐篇抓詳情頁全文摘要(7家台廠個別結構都不同，全文抓取成本過高)，比照
main.py DIGEST_CHANNELS的tsmc_digest/cbc_digest模式，summary留給main.py
的summarize_fn決定(這裡選擇直接回傳None，只送標題給Gemini做交叉比對，
多數公告標題本身已足夠說明)。
"""
from scrapers import semi_tw_suppliers

SOURCE_IDS = semi_tw_suppliers.SOURCE_IDS


def fetch() -> list[dict]:
    return semi_tw_suppliers.fetch_all()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items = fetch()
    print(f"共 {len(items)} 篇（台廠）")
    for item in items:
        print(f"[{item['source_name']}] {item['title']}")
