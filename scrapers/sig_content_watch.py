"""sig_content_watch.py — 留學德國社群網站(study-in-germany/sig)內容維護監測。

2026-08-05新增。sig的權威參考資料(簽證/獎學金/申請流程官方連結、學校
官網、金融服務連結)是寫死在該repo的靜態TS/JSON檔裡(src/data/edu/*.ts、
src/data/schools.json、src/data/recommendations/finance.json)，不在
Supabase資料庫，也沒有任何自動保鮮機制。

範圍刻意縮小過(使用者2026-08-05對話中只確認了這兩項，原本討論過的「官方
頁面內容變動偵測(hash比對)」尚未取得明確同意，故未實作)：
1. 死連結檢查——official_sources/website/url三類連結定期打一次，沿用
   db.record_fetch_success/record_fetch_failure既有的source表fail_count
   機制(跟check_triggers.py同一套重試退避邏輯)，連續失敗>=2次才視為真的
   死了(避免單次網路抖動誤判)
2. updated_at過期提醒——純日期比對(schools.json/finance.json每筆都有
   updated_at)，超過12個月沒更新就列入提醒，不需要任何持久化狀態

不動任何Supabase schema，也不需要ip自己的db.py新增資料表——沿用既有
source表的fail_count/last_fetched_at欄位，source_id用網址雜湊當key。
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

import db

SOURCE_NAME = "留德網站內容維護監測"
SOURCE_ID = "sig_content_watch"

SIG_DATA_ROOT = Path(r"C:\Projects\10-101_Study_in_Germany_留學德國社群網站\src\data")
EDU_DIR = SIG_DATA_ROOT / "edu"
SCHOOLS_JSON = SIG_DATA_ROOT / "schools.json"
FINANCE_JSON = SIG_DATA_ROOT / "recommendations" / "finance.json"

STALE_MONTHS = 12
DEAD_LINK_FAIL_THRESHOLD = 2
REQUEST_TIMEOUT = 15

_URL_RE = re.compile(r"url:\s*'([^']+)'")
_DATE_RE = re.compile(r"updated_at:\s*'([^']+)'")


def _extract_edu_links() -> list[tuple[str, str]]:
    """回傳[(來源標籤, url), ...]。粗粒度用regex抽取(不逐步對應到哪個
    step)，維護用途夠了，不值得為此另外寫TS parser。"""
    links = []
    if not EDU_DIR.is_dir():
        return links
    for ts_file in sorted(EDU_DIR.glob("*.ts")):
        text = ts_file.read_text(encoding="utf-8")
        for url in _URL_RE.findall(text):
            links.append((f"edu/{ts_file.name}", url))
    return links


def _extract_edu_dates() -> list[tuple[str, str]]:
    """回傳[(來源標籤, updated_at字串), ...]。"""
    dates = []
    if not EDU_DIR.is_dir():
        return dates
    for ts_file in sorted(EDU_DIR.glob("*.ts")):
        text = ts_file.read_text(encoding="utf-8")
        for d in _DATE_RE.findall(text):
            dates.append((f"edu/{ts_file.name}", d))
    return dates


def _load_json_entries(path: Path, url_field: str, label_field: str) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"label": item.get(label_field, "?"), "url": item.get(url_field), "updated_at": item.get("updated_at")}
        for item in data
        if item.get(url_field)
    ]


def collect_links() -> list[tuple[str, str]]:
    """所有要做死連結檢查的(來源標籤, url)。"""
    links = _extract_edu_links()
    links += [(f"schools.json:{e['label']}", e["url"]) for e in _load_json_entries(SCHOOLS_JSON, "website", "name_zh")]
    links += [(f"finance.json:{e['label']}", e["url"]) for e in _load_json_entries(FINANCE_JSON, "url", "title")]
    # 去重(同一網址可能在多處被引用)，保留第一個出現的標籤
    seen = {}
    for label, url in links:
        seen.setdefault(url, label)
    return [(label, url) for url, label in seen.items()]


def collect_dated_entries() -> list[dict]:
    """所有要做updated_at過期檢查的項目。edu/*.ts的updated_at是逐步驟的，
    這裡直接用檔名當標籤(不細分到哪一步)。"""
    entries = [{"label": label, "updated_at": d} for label, d in _extract_edu_dates()]
    entries += [{"label": f"schools.json:{e['label']}", "updated_at": e["updated_at"]}
                for e in _load_json_entries(SCHOOLS_JSON, "website", "name_zh") if e.get("updated_at")]
    entries += [{"label": f"finance.json:{e['label']}", "updated_at": e["updated_at"]}
                for e in _load_json_entries(FINANCE_JSON, "url", "title") if e.get("updated_at")]
    return entries


def _url_source_id(url: str) -> str:
    return "sig_watch:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# 2026-08-05測試時實測踩到的坑：daad.de/auswaertiges-amt.de/revolut.com這些
# 明顯活著的知名網站，用簡陋的requests.get(只帶User-Agent: Mozilla/5.0)
# 打會被擋(403/連線被重置)，不是真的死了，是反爬蟲機制在擋「看起來不像
# 瀏覽器」的請求。改用完整瀏覽器標頭+單次檢查內重試一次再判定，並且把
# 403/429這種「被擋」跟404/410這種「真的沒了」分開——403/429不算失敗，
# 避免把「網站不歡迎爬蟲」誤判成「連結失效」。
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,de;q=0.7",
}
_INCONCLUSIVE_STATUS = {403, 429}


def _probe_url(url: str) -> bool:
    """單一URL存活判定。403/429視為「被反爬蟲擋下,無法判斷」，一律當作
    存活(寧可漏抓真死連結,不要把正常網站誤判成死掉)。單次檢查內重試一次
    (間隔2秒)才真正判定失敗,降低單次網路抖動/暫時性擋擋誤判。"""
    import time
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=_BROWSER_HEADERS, allow_redirects=True)
            if resp.status_code in _INCONCLUSIVE_STATUS:
                return True
            if resp.status_code < 400:
                return True
        except requests.RequestException:
            pass
        if attempt == 0:
            time.sleep(2)
    return False


def check_dead_links(links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """回傳這次判定為死連結的[(標籤, url), ...]（fail_count達門檻才算，
    不是單次失敗就報)。"""
    dead = []
    for label, url in links:
        source_id = _url_source_id(url)
        db.upsert_source(source_id, label, "sig_watch", url)
        ok = _probe_url(url)

        if ok:
            db.record_fetch_success(source_id)
        else:
            fail_count = db.record_fetch_failure(source_id)
            if fail_count >= DEAD_LINK_FAIL_THRESHOLD:
                dead.append((label, url))
    return dead


def check_stale_entries(entries: list[dict]) -> list[tuple[str, str]]:
    """回傳超過STALE_MONTHS沒更新的[(標籤, updated_at), ...]。純日期比對，
    不需要任何持久化狀態。"""
    now = datetime.now(timezone.utc)
    stale = []
    for e in entries:
        raw = e["updated_at"]
        try:
            # 支援'2026-07'跟'2026-07-11'兩種格式(前者edu用月份即可,後者schools/finance帶完整日期)
            parts = raw.split("-")
            dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 1, tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
        months_old = (now.year - dt.year) * 12 + (now.month - dt.month)
        if months_old >= STALE_MONTHS:
            stale.append((e["label"], raw))
    return stale


def run_check(dry_run: bool = False) -> str | None:
    """執行一次完整檢查，回傳要推播的訊息文字；沒有任何異常時回傳None
    (避免每週固定推播「一切正常」造成通知疲勞)。"""
    links = collect_links()
    dated = collect_dated_entries()

    dead = check_dead_links(links) if not dry_run else []
    stale = check_stale_entries(dated)

    if not dead and not stale:
        return None

    lines = [f"🇩🇪 留德網站內容維護提醒｜{datetime.now().strftime('%Y-%m-%d')}"]
    if dead:
        lines.append(f"\n🔴 疑似失效連結（連續{DEAD_LINK_FAIL_THRESHOLD}次檢查失敗）：")
        lines += [f"・{label}\n  {url}" for label, url in dead]
    if stale:
        lines.append(f"\n🟡 超過{STALE_MONTHS}個月未更新，建議人工複查：")
        lines += [f"・{label}（{updated_at}）" for label, updated_at in stale]
    return "\n".join(lines)
