"""
scrapers/house_591.py — 591租屋網找房頻道，純規則式篩選，零AI。

2026-08-01實測確認(用Claude in Chrome看真實DOM/URL，不是猜的)：
- 591清單頁是伺服器端渲染，篩選條件直接編碼在URL query params，不需要
  額外找JSON API：
    region=8(台中市)、section=104,121,101(西屯區/龍井區/西區)、
    rentprice=下限_上限、option=icebox,broadband(冰箱/網路)、
    notice=all_sex,girl(男女皆可+限女生，排除限男生)
- **591的rentprice篩選只看「顯示租金」，不含額外費用**——實測一筆顯示
  租金3,799但備註「額外費用1,001元/月」「租金+額外費用合計4,800」，
  URL層的rentprice=0_5000不會擋到「顯示租金過關但額外費用推高總額」的
  物件。因此URL層故意放寬到0_8000當粗篩，實際硬性排除改用解析出來的
  「租金+額外費用」真實總額再判斷≤5000(使用者2026-08-01確認採用這個
  做法，見_parse_total_cost())
- 清單卡片(class="item")本身就有「可開伙」「租金補貼」這類文字標籤
  (class="tag"或class含"label")可以直接抓，不需要額外請求
- 公車站距離資訊實測只有進到物件內頁(每筆額外一次請求)才有，清單頁抓
  不到——使用者2026-08-01確認這個加分條件不值得為此多打N次請求，故意
  不做，只留「東海」關鍵字比對當地緣性的簡易加分
- 這支程式碼base在Claude in Chrome的即時DOM探測結果撰寫，還沒在真實
  requests+BeautifulSoup環境跑過完整流程(這個開發環境的Bash工具連不到
  591.com.tw，見[[feedback_sandbox_network_blocked_use_browser_download]]
  同一類限制)——正式排程會跑在使用者自己的機器上，不受這個限制，但
  首次上線建議使用者手動跑一次`python scrapers/house_591.py`確認selector
  沒有失效。
"""
import re

import requests
import truststore
from bs4 import BeautifulSoup

# 591.com.tw憑證鏈實測會撞跟cbc.gov.tw/MOL同一種「缺Subject Key
# Identifier」SSL驗證錯誤(2026-08-01實測)，改用系統信任庫，不是政府網站
# 專屬問題——同一支模組獨立呼叫(不依賴main.py先呼叫過)，比照
# internship_mol.py的既有模式，這樣單獨跑`python -m scrapers.house_591`
# 測試時也不會漏掉。
truststore.inject_into_ssl()

SOURCE_ID = "house.591.taichung_west"
SOURCE_NAME = "591租屋網(西屯/龍井/西區)"

LIST_URL = (
    "https://rent.591.com.tw/list"
    "?region=8&rentprice=0_8000&section=104,121,101"
    "&option=icebox,broadband&notice=all_sex,girl"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

MAX_TOTAL_COST = 5000
BONUS_TAG_KEYWORDS = ("可開伙", "租金補貼")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 3
TIMEOUT_SECONDS = 15

# 「租金+額外費用合計4,800元/月」這種有合計字樣時，取合計數字當真實總額；
# 沒有合計字樣代表顯示租金本身就是全部費用，直接取開頭數字。
_TOTAL_RE = re.compile(r"合計\s*([\d,]+)\s*元/月")
_BASE_PRICE_RE = re.compile(r"^([\d,]+)\s*元/月")


def _parse_total_cost(price_text: str) -> int | None:
    if not price_text:
        return None
    total_match = _TOTAL_RE.search(price_text)
    if total_match:
        return int(total_match.group(1).replace(",", ""))
    base_match = _BASE_PRICE_RE.search(price_text)
    if base_match:
        return int(base_match.group(1).replace(",", ""))
    return None


def _get_with_retry() -> requests.Response:
    import time
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(LIST_URL, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    raise RuntimeError("unreachable")  # pragma: no cover


def fetch() -> list[dict]:
    """回傳符合硬性條件(地區/總費用≤5000/冰箱/網路/非限男生)的物件，
    依加分項(可開伙/租金補貼/東海關鍵字)排序，加分多的在前面。硬性條件
    已經在URL層做掉(地區/設備/性別)，這裡只再做URL層做不到的「真實總
    費用」硬性排除。"""
    resp = _get_with_retry()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for card in soup.select("div.item"):
        link = card.select_one("a.link")
        if not link:
            continue
        href = (link.get("href") or "").split("?")[0]
        title = link.get_text(strip=True)
        if not href or not title:
            continue

        price_el = card.select_one("[class*='price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        total_cost = _parse_total_cost(price_text)
        if total_cost is None or total_cost > MAX_TOTAL_COST:
            continue

        tags = [t.get_text(strip=True) for t in card.select(".tag, [class*='label']")]
        card_text = card.get_text()
        bonus_score = sum(1 for kw in BONUS_TAG_KEYWORDS if any(kw in tag for tag in tags))
        if "東海" in title or "東海" in card_text:
            bonus_score += 1

        items.append({
            "title": title,
            "url": href,
            "summary": f"{total_cost}元/月(含額外費用) | 標籤：{'、'.join(tags) if tags else '無'}",
            "published_at": None,
            "_bonus_score": bonus_score,
        })

    items.sort(key=lambda i: i["_bonus_score"], reverse=True)
    return items


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    results = fetch()
    print(f"共 {len(results)} 筆符合條件的物件")
    for r in results[:10]:
        print(f"  • [{r['_bonus_score']}分] {r['title'][:40]}")
        print(f"    {r['summary']}")
        print(f"    {r['url']}")
