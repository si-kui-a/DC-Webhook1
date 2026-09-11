"""
scrapers/semi_tw_suppliers.py — 台積電上下游台廠供應鏈新聞(半導體供應鏈頻道)。

範圍(使用者2026-07-30確認)：只做台積電的上下游台廠，不含台積電本身
(已有獨立頻道，見scrapers/tsmc.py)。白名單7家(來自stub文件既有研究，
逐一驗證官網IR/新聞頁可用性，非MOPS——MOPS重大訊息查詢需ASP.NET
ViewState/Session，成本過高，見semi_supply_chain.py舊docstring)：
- 3711 日月光投控(封測)　- 2449 京元電子(測試)
- 3131 弘塑科技(濕製程設備) - 6187 萬潤科技(自動化設備)
- 3680 家登精密(EUV載具)  - 3583 辛耘企業(半導體設備)
- 1560 中砂(研磨/再生晶圓)

各公司官網結構差異大，比照tsmc.py的requests+BeautifulSoup模式，
7家各寫一支fetch函式；官方網址透過TWSE/TPEx開放資料(t187ap03_L/
mopsfin_t187ap03_O)的「網址」欄位取得，不是用搜尋引擎猜測(TPEx需
truststore.inject_into_ssl()，見PAT-01同類Missing Subject Key
Identifier問題，但這只在查詢網址欄位時用到，各公司官網本身不需要)。

只做「新聞列表页」，不含逐篇全文摘要(7家結構皆不同，全文抓取成本
高，且多為公告標題已足夠說明，如「公告本公司董事會決議分派現金
股利」)——summary留空，比照tsmc_digest/cbc_digest的normalize
階段(main.py的_normalize_tsmc同樣不含summary，摘要由summarize_fn
另外處理；這裡選擇讓summarize_fn回傳None，不逐篇抓詳情頁)。
"""
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrapers import http_client

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _fetch_ase() -> list[dict]:
    """3711 日月光投控。英文版press-room是Webflow CMS伺服器渲染清單
    (中文版/ch/press-room疑似同架構但未逐一比對，先用英文版)。"""
    source_id, source_name = "semi_tw.ase", "日月光投控-新聞"
    base_url = "https://www.aseglobal.com"
    resp = http_client.get(f"{base_url}/press-room", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for item in soup.select("div.mc-item"):
        link_el = item.select_one("a.article-link[href]")
        title_el = item.select_one("h2.mc_item-name")
        if not link_el or not title_el:
            continue
        date_el = item.select_one(".mc-time.date")
        year_el = item.select_one(".mc-time.fs_cmsnest_label")
        published_at = " ".join(
            filter(None, [date_el.get_text(strip=True) if date_el else None,
                          year_el.get_text(strip=True) if year_el else None])
        ) or None
        results.append({
            "title": title_el.get_text(strip=True),
            "summary": None,
            "url": urljoin(base_url, link_el.get("href", "")),
            "published_at": published_at,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


def _fetch_kyec() -> list[dict]:
    """2449 京元電子。"""
    source_id, source_name = "semi_tw.kyec", "京元電子-新聞中心"
    base_url = "https://www.kyec.com.tw"
    resp = http_client.get(f"{base_url}/zh-tw/News", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for row in soup.select("a.tb-row.news-list[href]"):
        title_el = row.select_one('[data-field="標題"]')
        date_el = row.select_one('[data-field="年度"]')
        if not title_el:
            continue
        results.append({
            "title": title_el.get_text(strip=True),
            "summary": None,
            "url": urljoin(base_url, row.get("href", "")),
            "published_at": date_el.get_text(strip=True) if date_el else None,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


def _fetch_gptc() -> list[dict]:
    """3131 弘塑科技。"""
    source_id, source_name = "semi_tw.gptc", "弘塑科技-新聞中心"
    base_url = "https://www.gptc.com.tw"
    resp = http_client.get(f"{base_url}/news/", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for txt_box in soup.select("div.Txt"):
        link_el = txt_box.select_one("h3.itemTitle a[href]")
        date_el = txt_box.select_one("div.dateBox .date")
        if not link_el:
            continue
        results.append({
            "title": link_el.get_text(strip=True),
            "summary": None,
            "url": urljoin(base_url, link_el.get("href", "")),
            "published_at": date_el.get_text(strip=True) if date_el else None,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


def _fetch_allring() -> list[dict]:
    """6187 萬潤科技。"""
    source_id, source_name = "semi_tw.allring", "萬潤科技-新聞公告"
    base_url = "https://www.allring-tech.com.tw"
    resp = http_client.get(f"{base_url}/news.htm", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for item in soup.select("a.newsList__item[href]"):
        title_el = item.select_one("h4.newsTit")
        date_el = item.select_one("span.newsListDate")
        if not title_el:
            continue
        results.append({
            "title": title_el.get_text(strip=True),
            "summary": None,
            "url": urljoin(f"{base_url}/", item.get("href", "")),
            "published_at": date_el.get_text(strip=True) if date_el else None,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


def _fetch_gudeng() -> list[dict]:
    """3680 家登精密。用「重大訊息公告」頁(itemid=24)而非一般行銷新聞，
    跟半導體供應鏈頻道的重大訊息屬性較貼近。"""
    source_id, source_name = "semi_tw.gudeng", "家登精密-重大訊息"
    base_url = "https://www.gudeng.com"
    resp = http_client.get(f"{base_url}/Message?itemid=24&mid=89", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for item in soup.select("a[href*='MessageView']"):
        title_el = item.select_one("div.con div.title")
        date_el = item.select_one("div.con div.date")
        if not title_el:
            continue
        # div.date內還嵌了一個span.type(民國年度分類標籤,如"115年度")，
        # get_text會把兩者黏在一起變成"2026/05/27115年度"，只取date本身
        # 的第一個直接文字節點(不含子標籤)才是乾淨的西元日期。
        published_at = None
        if date_el:
            direct_text = date_el.find(string=True, recursive=False)
            published_at = direct_text.strip() if direct_text else None
        results.append({
            "title": title_el.get_text(strip=True),
            "summary": None,
            "url": urljoin(base_url, item.get("href", "")),
            "published_at": published_at,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


_SCIENTECH_DATE_PREFIX_RE = re.compile(r"^(\d{4}/\d{2}/\d{2})(.*)$")


def _fetch_scientech() -> list[dict]:
    """3583 辛耘企業。標題文字是「日期+標題」直接相連(無分隔符)，
    需用開頭的YYYY/MM/DD正則切開，比照tsmc.py DATELINE_RE的資料
    清洗做法。"""
    source_id, source_name = "semi_tw.scientech", "辛耘企業-最新消息"
    base_url = "https://www.scientech.com.tw"
    resp = http_client.get(f"{base_url}/zh-hant/PressCenter/News/LatestNews", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for link_el in soup.select("h3.node__title a[href]"):
        raw_text = link_el.get_text(strip=True)
        m = _SCIENTECH_DATE_PREFIX_RE.match(raw_text)
        published_at, title = (m.group(1), m.group(2)) if m else (None, raw_text)
        if not title:
            continue
        results.append({
            "title": title,
            "summary": None,
            "url": urljoin(base_url, link_el.get("href", "")),
            "published_at": published_at,
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


def _fetch_kinik() -> list[dict]:
    """1560 中砂。官網是Angular SPA，新聞資料走乾淨的JSON API
    (非公開文件的內部端點，實測curl驗證可用，見研究記錄)，
    不需要BeautifulSoup解析HTML。"""
    source_id, source_name = "semi_tw.kinik", "中砂-新聞中心"
    base_url = "https://www.kinik.com.tw"
    resp = http_client.get(f"{base_url}/APIHtml/news/tw", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for row in data:
        title = (row.get("Title") or "").strip()
        item_id = row.get("ID")
        if not title or item_id is None:
            continue
        results.append({
            "title": title,
            "summary": None,
            "url": f"{base_url}/zh-tw/News/NewsDetail.html?id={item_id}",
            "published_at": row.get("PublishTime"),
            "source_id": source_id,
            "source_name": source_name,
        })
    return results


# (股票代號, source_id, 顯示名稱, fetch函式) —— main.py的source_ids/
# DIGEST_FIRST_RUN_CAP判斷需要逐一列出每家的source_id，故保留這份清單
# 供main.py引用；source_id重複列一次(而非從fetch_fn結果反查)是為了
# 讓單一公司fetch_fn整支失敗時，也能回報「對得上號」的錯誤佔位項目。
COMPANY_FETCHERS = [
    ("3711", "semi_tw.ase", "日月光投控", _fetch_ase),
    ("2449", "semi_tw.kyec", "京元電子", _fetch_kyec),
    ("3131", "semi_tw.gptc", "弘塑科技", _fetch_gptc),
    ("6187", "semi_tw.allring", "萬潤科技", _fetch_allring),
    ("3680", "semi_tw.gudeng", "家登精密", _fetch_gudeng),
    ("3583", "semi_tw.scientech", "辛耘企業", _fetch_scientech),
    ("1560", "semi_tw.kinik", "中砂", _fetch_kinik),
]

SOURCE_IDS = [source_id for _, source_id, _, _ in COMPANY_FETCHERS]


def fetch_all() -> list[dict]:
    """抓取全部7家，單一公司失敗不中斷其他公司(比照us_customer_feeds.
    fetch_all的容錯設計)。"""
    all_items = []
    for code, source_id, name, fetch_fn in COMPANY_FETCHERS:
        try:
            all_items.extend(fetch_fn())
        except Exception as e:
            # PAT-24：title/url必須帶當天日期，否則title(靜態)+url("")的
            # dedup_key每天都一樣，db.insert_item_if_new()第二天起會把
            # 失敗當成「已推播過」直接吞掉——真正的抓取中斷只會被看到
            # 一次，之後就靜默消失，見db.py make_dedup_key()。
            all_items.append({
                "title": f"{name}({code}) - 抓取失敗（{date.today().isoformat()}）",
                "summary": f"無法取得資料：{e}",
                "url": "",
                "published_at": None,
                "source_id": source_id,
                "source_name": name,
            })
    return all_items


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    items = fetch_all()
    print(f"共 {len(items)} 篇（跨 {len(COMPANY_FETCHERS)} 家公司）")
    for item in items:
        print(f"[{item['source_name']}] {item['title']} ({item['published_at']})")
