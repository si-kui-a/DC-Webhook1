"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import logging
import os
from datetime import datetime

import db
from push_webhook import build_embed, send_webhook
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util
from jobs.paths import SOURCE_HEALTH_PATH

logger = logging.getLogger("main")

# 獎學金來源（批次模式，共用一個 webhook URL，推播合併為一條訊息）
SCHOLARSHIP_REGISTRY = {
    "scholarship_daad": (scholarship_daad.fetch, scholarship_daad.SOURCE_NAME, scholarship_daad.SOURCE_ID),
    "scholarship_moe": (scholarship_moe.fetch, scholarship_moe.SOURCE_NAME, scholarship_moe.SOURCE_ID),
    "scholarship_thu": (scholarship_thu.fetch, scholarship_thu.SOURCE_NAME, scholarship_thu.SOURCE_ID),
    "scholarship_efg": (scholarship_efg.fetch, scholarship_efg.SOURCE_NAME, scholarship_efg.SOURCE_ID),
}
SCHOLARSHIP_WEBHOOK_ENV = "WEBHOOK_SCHOLARSHIP"
# 首次執行安全閘門：每來源最多推播 N 筆（避免洗版）
SCHOLARSHIP_FIRST_RUN_CAP = 20
def _build_scholarship_batch(items_by_source: dict[str, list[dict]]) -> list[str]:
    """
    將各來源的新項目組裝成一條或多條 Discord/Telegram 批次訊息。
    每條 ≤ 3900 chars 避免超過平台限制。回傳字串列表。
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    full_lines = [f"📚 *獎學金快報* | {date_str}", ""]

    first = True
    for source_name, items in items_by_source.items():
        if not items:
            continue
        if not first:
            full_lines.append("")
        first = False
        full_lines.append("━━━━━━━━━━━━━")
        full_lines.append(f"*【{source_name}】*（{len(items)} 筆）")
        for item in items:
            title = item["title"][:120]
            url = item.get("url", "")
            full_lines.append(f"• [{title}]({url})" if url else f"• {title}")

    full_text = "\n".join(full_lines)
    if not full_text.strip():
        return []

    # 單條塞得下就直接回傳
    if len(full_text) <= 3900:
        return [full_text]

    # 塞不下就依來源拆成多條
    header_lines = [f"📚 *獎學金快報*（續）| {date_str}", ""]
    chunks = []
    # 第一條包含完整 header
    current = [f"📚 *獎學金快報* | {date_str}", ""]

    source_groups = list(items_by_source.items())
    for idx, (source_name, items) in enumerate(source_groups):
        if not items:
            continue
        block = [
            "" if idx == 0 else "",
            "━━━━━━━━━━━━━",
            f"*【{source_name}】*（{len(items)} 筆）",
        ]
        if idx > 0:
            block = block[1:]  # 非第一條不需要開頭空行
        for item in items:
            title = item["title"][:120]
            url = item.get("url", "")
            block.append(f"• [{title}]({url})" if url else f"• {title}")

        # 試著加入目前 chunk，超過就開新 chunk
        candidate = current + block
        if len("\n".join(candidate)) > 3900:
            chunks.append("\n".join(current))
            current = list(header_lines) + block
        else:
            current = candidate

    if current:
        chunks.append("\n".join(current))
    return chunks


def run_scholarship():
    """批次執行所有獎學金來源，收集新項目後合併推播。"""
    webhook_url = os.getenv(SCHOLARSHIP_WEBHOOK_ENV)
    if not webhook_url:
        logger.error("缺少環境變數 %s，跳過獎學金批次", SCHOLARSHIP_WEBHOOK_ENV)
        return

    # 每次執行前重載設定檔（確保 profile/exclude/keywords 即時生效）
    scholarship_util.invalidate_cache()

    scholarship_keys = list(SCHOLARSHIP_REGISTRY.keys())
    is_first_run = all(db.count_items_for_source(info[2]) == 0 for _, info in SCHOLARSHIP_REGISTRY.items())

    new_items_by_source: dict[str, list[dict]] = {}
    total_new = 0

    for key in scholarship_keys:
        fetch_fn, source_name, source_id = SCHOLARSHIP_REGISTRY[key]
        db.upsert_source(source_id, source_name, "scholarship", "")

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

        new_for_source = []
        push_cap = SCHOLARSHIP_FIRST_RUN_CAP if is_first_run else None
        pushed_count = 0

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

            # 完整過濾管線：硬性排除（戶籍地/學校/收入/國籍/特殊身份/年級）
            # + 關鍵字相關性計分。未通過的項目標記為歷史存檔，不推播。
            profile = scholarship_util.load_profile()
            if (scholarship_util.is_excluded_by_keywords(raw["title"], profile)
                or scholarship_util.is_excluded_by_residence(raw["title"], profile)
                or scholarship_util.is_excluded_by_school(raw["title"], profile)
                or scholarship_util.is_excluded_by_income(raw["title"], profile)
                or scholarship_util.is_excluded_by_nationality(raw["title"], profile)
                or scholarship_util.is_excluded_by_special_status(raw["title"], profile)
                or scholarship_util.is_excluded_by_grade(raw["title"], profile)
                or not scholarship_util.is_relevant(raw["title"])):
                db.mark_seeded_historical(item["item_id"])
                logger.debug("[%s] 過濾排除，跳過: %s", key, raw["title"][:60])
                continue

            if push_cap is not None and pushed_count >= push_cap:
                db.mark_seeded_historical(item["item_id"])
                continue

            new_for_source.append(item)
            pushed_count += 1

        if new_for_source:
            new_items_by_source[source_name] = new_for_source
            total_new += len(new_for_source)
            logger.info("[%s] 新項目 %d 筆", key, len(new_for_source))

    if total_new == 0:
        logger.info("獎學金批次完成，無新項目")
        return

    # 組裝批次訊息
    chunks = _build_scholarship_batch(new_items_by_source)
    if not chunks:
        logger.warning("獎學金批次文字組裝失敗（可能為空）")
        return

    # 推播到 Discord（每條 chunk 一個 embed）
    all_ok = True
    for i, chunk in enumerate(chunks):
        embed = build_embed(
            title=f"📚 獎學金快報 {'（續）' if i > 0 else ''}",
            description=chunk,
            url="",
            footer="獎學金監控",
        )
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("獎學金批次推播成功（chunk %d/%d）", i + 1, len(chunks))
        else:
            all_ok = False
            logger.error("獎學金批次推播失敗（chunk %d/%d）: HTTP %s %s", i + 1, len(chunks), status, err)

    # 標記已推播——原本這裡漏掉這一步，status一直卡在'new'，正是本專案
    # Meta_Dev_Knowledge.md PAT-03警告過的「status雙重語意風險」(2026-07-30
    # 發現)：去重靠dedup_key不受影響，但語意不誠實，未來若有功能誤用
    # status='new'找待處理項目會誤判成這些已經推播過的項目還沒處理。
    if all_ok:
        for items in new_items_by_source.values():
            for item in items:
                db.mark_published(item["item_id"])

    # Telegram簡短通知已於2026-09-10移除(使用者確認)：教育類EDU bot改為
    # jobs/thu_calendar.py的東海行事曆合併用途，獎學金完整內容維持只在
    # Discord頻道推播(見上方webhook推播)，不再重複發Telegram。
