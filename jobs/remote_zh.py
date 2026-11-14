"""中文母語遠端頻道(2026-11-14新增，使用者確認)：把需要中文的全遠端職缺推到獨立 webhook。

不經 internship_util 的實習關鍵字/行業排除——這個頻道的重點就是翻譯/口譯/客服/AI訓練
這類語言職缺，套實習規則會被整批濾光。去重靠 db.insert_item_if_new；第一次執行
只推 REMOTE_ZH_FIRST_RUN_CAP 筆，其餘標成歷史，避免一次洗版。
"""
import logging
import os

import db
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import remote_zh_himalayas
from scrapers import remote_zh_arbeitnow
from jobs.internship import _push_internship_style_batch
from jobs.paths import SOURCE_HEALTH_PATH

logger = logging.getLogger("main")

REMOTE_ZH_WEBHOOK_ENV = "WEBHOOK_REMOTE_ZH"
REMOTE_ZH_FIRST_RUN_CAP = 15
REMOTE_ZH_SOURCES = {
    "remote_zh_himalayas": (
        remote_zh_himalayas.fetch, remote_zh_himalayas.SOURCE_NAME, remote_zh_himalayas.SOURCE_ID,
    ),
    "remote_zh_arbeitnow": (
        remote_zh_arbeitnow.fetch, remote_zh_arbeitnow.SOURCE_NAME, remote_zh_arbeitnow.SOURCE_ID,
    ),
}


def run_remote_zh():
    webhook = os.getenv(REMOTE_ZH_WEBHOOK_ENV)
    if not webhook:
        logger.error("缺少環境變數 %s，跳過中文母語遠端批次", REMOTE_ZH_WEBHOOK_ENV)
        return False

    is_first_run = all(db.count_items_for_source(info[2]) == 0 for info in REMOTE_ZH_SOURCES.values())
    items_by_source: dict[str, list[dict]] = {}
    total = 0

    for key, (fetch_fn, source_name, source_id) in REMOTE_ZH_SOURCES.items():
        db.upsert_source(source_id, source_name, "remote_zh", "")
        try:
            raw_items = fetch_fn()
        except Exception as e:
            fail_count = db.record_fetch_failure(source_id)
            record_source_health(SOURCE_HEALTH_PATH, source_id, "FAILED", error=str(e))
            logger.error("[%s] 抓取失敗（累計 %d 次）: %s", key, fail_count, e)
            return False

        report = validate_items(raw_items)
        raw_items = report.accepted
        health_status = "PARTIAL" if report.rejected else ("EMPTY_VALID" if not raw_items else "SUCCESS")
        record_source_health(SOURCE_HEALTH_PATH, source_id, health_status,
                             item_count=len(raw_items), rejected_count=report.rejected)
        db.record_fetch_success(source_id)

        new_items = []
        for raw in raw_items:
            item = db.insert_item_if_new(
                source_id=source_id, title=raw["title"], summary=raw.get("summary"),
                url=raw["url"], published_at=raw.get("published_at"),
            )
            if item is None:
                continue
            if is_first_run and len(new_items) >= REMOTE_ZH_FIRST_RUN_CAP:
                db.mark_seeded_historical(item["item_id"])
            else:
                new_items.append(item)
        if new_items:
            items_by_source[source_name] = new_items
            total += len(new_items)
            logger.info("[%s] 中文母語遠端新項目 %d 筆", key, len(new_items))

    if total == 0:
        logger.info("中文母語遠端批次完成，無新項目")
        return True

    _push_internship_style_batch(
        webhook, items_by_source, total,
        embed_title="🌏 中文母語遠端快報", footer="中文母語遠端監控",
    )
    return True
