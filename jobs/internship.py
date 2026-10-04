"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import logging
import os
from datetime import datetime

import db
from push_webhook import build_embed, send_webhook
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import internship_mol
from scrapers import internship_104
from scrapers import internship_rich
from scrapers import internship_yes123
from scrapers import internship_gift
from scrapers import internship_util
from jobs.paths import SOURCE_HEALTH_PATH

logger = logging.getLogger("main")

# 台灣實習頻道(比照獎學金頻道模式)。科系/資歷/學校/年級/國籍/身份別排除
# 規則已套用(2026-07-31，見internship_util.is_relevant())。104用關鍵字
# "實習"直接呼叫官方站內搜尋API(見internship_104.py)，命中率比MOL的
# 全量快照+本地篩選高很多(實測約77% vs 1%)，因為104自己的搜尋引擎已經
# 先做過一次相關性排序。518(internship_518.py)於2026-08-07移除：備而
# 不用觀察一週，累積43筆全數被篩掉，命中率確認0%，達到預設移除標準。
# GIFT(internship_gift.py)於2026-09-24比照移除：搜尋頁網址HTTP 404連續
# 24次以上(本機與GitHub runner都一樣)，scraper檔案保留供網址修好後復用。
# 2026-10-04加回：原因是網站分頁改成?page=N(舊的/jobs/N才404)，修好後
# 實測3頁30筆、全部不重複。
# (ops/source_health.json雖標了disabled，但沒有任何程式讀它，不會生效。)
# 1111人力銀行有主動的CAPTCHA/反爬蟲挑戰機制(altcha widget)，明確不做
# (見2026-07-31對話紀錄的界線說明)。
#
# registry值的第4個欄位skip_keyword_gate：RICH(教育部青年署見習/工讀
# 平台，見internship_rich.py)整體只有十幾筆職缺，且官方用語是「見習/
# 工讀」不是「實習」，用「實習」關鍵字計分門檻會把整個來源擋光——這個
# 來源改用internship_util.passes_profile_filters()，跳過關鍵字門檻，
# 只套用學校/年級/國籍/身份別/科系/行業別排除規則。
INTERNSHIP_REGISTRY = {
    "internship_mol": (internship_mol.fetch, internship_mol.SOURCE_NAME, internship_mol.SOURCE_ID, False),
    "internship_104": (internship_104.fetch, internship_104.SOURCE_NAME, internship_104.SOURCE_ID, False),
    "internship_rich": (internship_rich.fetch, internship_rich.SOURCE_NAME, internship_rich.SOURCE_ID, True),
    "internship_yes123": (internship_yes123.fetch, internship_yes123.SOURCE_NAME, internship_yes123.SOURCE_ID, False),
    "internship_gift": (internship_gift.fetch, internship_gift.SOURCE_NAME, internship_gift.SOURCE_ID, False),
}
INTERNSHIP_WEBHOOK_ENV = "WEBHOOK_INTERNSHIP"
INTERNSHIP_FIRST_RUN_CAP = 20

# 台灣求職頻道(2026-07-31新增，使用者確認)：跟實習頻道共用INTERNSHIP_
# REGISTRY同一批來源，同一次fetch分兩桶分別推播(見run_internship())，
# 不另外增加對外API呼叫量。104/518/yes123/gift的原始資料本來就是用
# 「實習」關鍵字搜出來的，套用internship_util.is_relevant_job_search()
# (反向排除實習關鍵字)後幾乎全數會被排除，這個頻道實質上以MOL(全國
# 職缺無關鍵字限制的廣泛快照)為主要供稿來源，使用者已確認接受此取捨。
JOB_SEARCH_WEBHOOK_ENV = "WEBHOOK_JOB_SEARCH"
JOB_SEARCH_FIRST_RUN_CAP = 20
def _build_internship_batch(items_by_source: dict[str, list[dict]], title: str = "💼 台灣實習快報") -> list[str]:
    """比照_build_scholarship_batch()的組裝邏輯，只是文案改成實習。title
    參數(2026-07-31新增)讓求職頻道(run_internship()裡的job_search分桶)
    重用同一組裝邏輯，不用複製一份幾乎一樣的函式。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    header = f"*{title}* | {date_str}"
    full_lines = [header, ""]

    for source_name, items in items_by_source.items():
        if not items:
            continue
        full_lines.append("━━━━━━━━━━━━━")
        full_lines.append(f"*【{source_name}】*（{len(items)} 筆）")
        for item in items:
            title_text = item["title"][:120]
            url = item.get("url", "")
            full_lines.append(f"• [{title_text}]({url})" if url else f"• {title_text}")

    full_text = "\n".join(full_lines)
    if not full_text.strip():
        return []
    if len(full_text) <= 3900:
        return [full_text]

    # 塞不下就每N筆拆一條(目前只有單一來源，不需要scholarship那套多來源分組邏輯)
    chunks = []
    current = [header, ""]
    for source_name, items in items_by_source.items():
        for item in items:
            title_text = item["title"][:120]
            url = item.get("url", "")
            line = f"• [{title_text}]({url})" if url else f"• {title_text}"
            if len("\n".join(current + [line])) > 3900:
                chunks.append("\n".join(current))
                current = [f"*{title}*（續）| {date_str}", "", line]
            else:
                current.append(line)
    if len(current) > 2:
        chunks.append("\n".join(current))
    return chunks


def _push_internship_style_batch(
    webhook_url: str,
    items_by_source: dict[str, list[dict]],
    total_new: int,
    embed_title: str,
    footer: str,
):
    """實習頻道/求職頻道共用的推播+標記邏輯(2026-07-31抽出，避免
    run_internship()裡兩個頻道各自複製一份幾乎一樣的程式碼)。Telegram
    簡短通知已於2026-09-10移除(使用者確認)：教育類EDU bot改為
    jobs/thu_calendar.py的東海行事曆合併用途。"""
    chunks = _build_internship_batch(items_by_source, title=embed_title)
    if not chunks:
        logger.warning("%s 文字組裝失敗（可能為空）", embed_title)
        return

    all_ok = True
    for i, chunk in enumerate(chunks):
        embed = build_embed(
            title=f"{embed_title}{'（續）' if i > 0 else ''}",
            description=chunk,
            url="",
            footer=footer,
        )
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("%s 推播成功（chunk %d/%d）", embed_title, i + 1, len(chunks))
        else:
            all_ok = False
            logger.error("%s 推播失敗（chunk %d/%d）: HTTP %s %s", embed_title, i + 1, len(chunks), status, err)

    # 標記已推播(同run_scholarship()的PAT-03修正，2026-07-30發現兩處都漏了)。
    if all_ok:
        for items in items_by_source.values():
            for item in items:
                db.mark_published(item["item_id"])


def run_internship():
    """台灣實習頻道+台灣求職頻道(2026-07-31新增求職頻道，使用者確認)：
    兩個頻道共用INTERNSHIP_REGISTRY同一批來源，同一次fetch分類成兩桶
    分別推播，不對外多打一次API。實習頻道全部篩選(關鍵字計分+學校/年級/
    國籍/身份別/科系/行業/語意噪音)在internship_util.is_relevant()完成；
    求職頻道用internship_util.is_relevant_job_search()——同一套profile/
    科系/行業排除規則，但反向排除任何命中實習關鍵字的項目，避免同一則
    職缺兩邊都推。skip_keyword_gate來源(RICH，本身是見習/工讀專屬平台)
    只服務實習頻道，不進求職頻道的候選池。零AI依賴(2026-07-31移除原本
    的Gemini語意消歧步驟，見internship_util.py模組docstring)。"""
    internship_webhook = os.getenv(INTERNSHIP_WEBHOOK_ENV)
    job_search_webhook = os.getenv(JOB_SEARCH_WEBHOOK_ENV)
    if not internship_webhook and not job_search_webhook:
        logger.error("缺少環境變數 %s 與 %s，跳過實習/求職批次", INTERNSHIP_WEBHOOK_ENV, JOB_SEARCH_WEBHOOK_ENV)
        return

    internship_util.invalidate_cache()
    is_first_run = all(db.count_items_for_source(info[2]) == 0 for info in INTERNSHIP_REGISTRY.values())

    internship_items_by_source: dict[str, list[dict]] = {}
    job_search_items_by_source: dict[str, list[dict]] = {}
    internship_total = 0
    job_search_total = 0

    for key, (fetch_fn, source_name, source_id, skip_keyword_gate) in INTERNSHIP_REGISTRY.items():
        db.upsert_source(source_id, source_name, "internship", "")
        try:
            raw_items = fetch_fn()
        except Exception as e:
            fail_count = db.record_fetch_failure(source_id)
            record_source_health(SOURCE_HEALTH_PATH, source_id, "FAILED", error=str(e))
            logger.error("[%s] 抓取失敗（累計 %d 次）: %s", key, fail_count, e)
            continue

        report = validate_items(raw_items)
        raw_items = report.accepted
        if report.rejected:
            logger.warning("[%s] PARTIAL rejected=%d errors=%s", key, report.rejected, report.errors[:3])
        health_status = "PARTIAL" if report.rejected else ("EMPTY_VALID" if not raw_items else "SUCCESS")
        record_source_health(SOURCE_HEALTH_PATH, source_id, health_status, item_count=len(raw_items), rejected_count=report.rejected)
        db.record_fetch_success(source_id)
        if not raw_items:
            logger.info("[%s] 無資料", key)
            continue

        internship_for_source = []
        job_search_for_source = []
        internship_cap = INTERNSHIP_FIRST_RUN_CAP if is_first_run else None
        job_search_cap = JOB_SEARCH_FIRST_RUN_CAP if is_first_run else None
        internship_pushed = 0
        job_search_pushed = 0

        for raw in raw_items:
            item = db.insert_item_if_new(
                source_id=source_id,
                title=raw["title"],
                summary=raw.get("summary"),
                url=raw["url"],
                published_at=raw.get("published_at"),
            )
            if item is None:
                continue  # 已存在

            filter_text = raw.get("_filter_text") or raw["title"]

            if skip_keyword_gate:
                is_internship = internship_util.passes_profile_filters(filter_text)
            else:
                is_internship = internship_util.is_relevant(filter_text, salary=raw.get("_salary_high"))

            if is_internship:
                if internship_cap is not None and internship_pushed >= internship_cap:
                    db.mark_seeded_historical(item["item_id"])
                else:
                    internship_for_source.append(item)
                    internship_pushed += 1
                continue

            # 不是實習才考慮求職頻道；skip_keyword_gate來源(RICH)本身定位
            # 就是見習/工讀專屬平台，不適合當一般職缺池，維持只服務實習
            # 頻道的角色。
            if skip_keyword_gate:
                db.mark_seeded_historical(item["item_id"])
                continue

            if not internship_util.is_relevant_job_search(filter_text):
                db.mark_seeded_historical(item["item_id"])
                continue

            if job_search_cap is not None and job_search_pushed >= job_search_cap:
                db.mark_seeded_historical(item["item_id"])
            else:
                job_search_for_source.append(item)
                job_search_pushed += 1

        if internship_for_source:
            internship_items_by_source[source_name] = internship_for_source
            internship_total += len(internship_for_source)
            logger.info("[%s] 實習頻道新項目 %d 筆", key, len(internship_for_source))
        if job_search_for_source:
            job_search_items_by_source[source_name] = job_search_for_source
            job_search_total += len(job_search_for_source)
            logger.info("[%s] 求職頻道新項目 %d 筆", key, len(job_search_for_source))

    if internship_total == 0 and job_search_total == 0:
        logger.info("實習/求職批次完成，無新項目")
        return

    if internship_total:
        if internship_webhook:
            _push_internship_style_batch(
                internship_webhook, internship_items_by_source, internship_total,
                embed_title="💼 台灣實習快報", footer="台灣實習監控",
            )
        else:
            logger.error("缺少環境變數 %s，實習頻道 %d 筆新項目未推播", INTERNSHIP_WEBHOOK_ENV, internship_total)

    if job_search_total:
        if job_search_webhook:
            _push_internship_style_batch(
                job_search_webhook, job_search_items_by_source, job_search_total,
                embed_title="🧑‍💼 台灣求職快報", footer="台灣求職監控",
            )
        else:
            logger.error("缺少環境變數 %s，求職頻道 %d 筆新項目未推播", JOB_SEARCH_WEBHOOK_ENV, job_search_total)
