"""
main.py — 主執行入口。

用法：
    python main.py --source tsmc
    python main.py --source fed
    python main.py --source cbc
    python main.py --source etf0050
    python main.py --source all

cron 排程範例見 crontab.example。
"""
import argparse
import logging
import os
import sys

import truststore

truststore.inject_into_ssl()  # 修 certifi 對某些政府網站(如 cbc.gov.tw)憑證鏈驗證過嚴的問題，改用系統信任庫

from dotenv import load_dotenv

import db
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(PROJECT_ROOT, "work")
os.makedirs(WORK_DIR, exist_ok=True)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_activity_handler = logging.FileHandler(os.path.join(WORK_DIR, "activity.log"), encoding="utf-8")
_activity_handler.setLevel(logging.INFO)
_activity_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

_error_handler = logging.FileHandler(os.path.join(WORK_DIR, "error.log"), encoding="utf-8")
_error_handler.setLevel(logging.ERROR)
_error_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    handlers=[logging.StreamHandler(), _activity_handler, _error_handler],
)
logger = logging.getLogger("main")

load_dotenv()

# source_id -> (fetch函式, 對應Webhook環境變數名稱, 顯示名稱)
SOURCE_REGISTRY = {
    "tsmc": (tsmc.fetch, "WEBHOOK_INSTITUTIONAL_TSMC", tsmc.SOURCE_NAME, tsmc.SOURCE_ID),
    "fed": (fed.fetch, "WEBHOOK_INSTITUTIONAL_FED", fed.SOURCE_NAME, fed.SOURCE_ID),
    "cbc": (cbc.fetch, "WEBHOOK_INSTITUTIONAL_CBC", cbc.SOURCE_NAME, cbc.SOURCE_ID),
    "etf0050": (etf0050.fetch, "WEBHOOK_INSTITUTIONAL_0050", etf0050.SOURCE_NAME, etf0050.SOURCE_ID),
}

# 連續失敗超過此次數，視為需要人工介入（用於未來接外部告警，本 MVP 先只記 log）
FAIL_THRESHOLD = 3

# 首次執行安全閘門：只套用在 cbc（RSS 一次回傳全部歷史，curl 實測約500筆，
# 資料庫是空的時候不能全部當「新項目」推播，否則洗版）。fed/tsmc 本來就是
# 個位數筆數，沒有這個風險，故不列在這裡（值為 None 代表不套用閘門）。
FIRST_RUN_PUSH_CAP = {
    "cbc": 5,
}


def _record_summary(key: str, how: str, result: str):
    """[WHY]/[HOW]/結果 三段式活動紀錄，寫進 work/activity.log。"""
    logger.info(f"[{key}] [WHY] 首次上線前的真實推播驗證 [HOW] {how} 結果：{result}")


def run_source(key: str):
    how = f"python main.py --source {key}"

    if key not in SOURCE_REGISTRY:
        logger.error(f"未知來源：{key}")
        return

    fetch_fn, webhook_env, source_name, source_id = SOURCE_REGISTRY[key]
    webhook_url = os.getenv(webhook_env)
    if not webhook_url:
        logger.error(f"缺少環境變數 {webhook_env}，跳過 {key}")
        _record_summary(key, how, f"失敗，缺少環境變數 {webhook_env}")
        return

    db.upsert_source(source_id, source_name, "institutional", "")
    is_first_run = db.count_items_for_source(source_id) == 0

    try:
        raw_items = fetch_fn()
    except NotImplementedError as e:
        logger.warning(f"[{key}] 尚未實作：{e}")
        _record_summary(key, how, f"尚未實作，未發送任何訊息（{e}）")
        return
    except Exception as e:
        fail_count = db.record_fetch_failure(source_id)
        logger.error(f"[{key}] 抓取失敗（累計 {fail_count} 次）：{e}", exc_info=True)
        if fail_count >= FAIL_THRESHOLD:
            logger.critical(f"[{key}] 已連續失敗 {fail_count} 次，需人工檢查 selector 是否因改版失效")
        _record_summary(key, how, f"例外失敗（累計{fail_count}次），完整 traceback 見 work/error.log")
        return

    db.record_fetch_success(source_id)

    if not raw_items:
        logger.info(f"[{key}] 本次無新資料")
        _record_summary(key, how, "無新資料（去重生效）")
        return

    # 只有第一次執行、且這個來源有設定 push cap 時才生效；非首次執行一律 None，
    # 走原本「只推播真正新增的項目」邏輯。
    push_cap = FIRST_RUN_PUSH_CAP.get(key) if is_first_run else None

    new_count = 0
    pushed_count = 0
    archived_only_count = 0
    for raw in raw_items:
        item = db.insert_item_if_new(
            source_id=source_id,
            title=raw["title"],
            summary=raw.get("summary"),
            url=raw["url"],
            published_at=raw.get("published_at"),
        )
        if item is None:
            continue  # 已存在，跳過（去重生效）

        new_count += 1

        if push_cap is not None and new_count > push_cap:
            # 已寫入 dedup_key 建立歷史記錄，但超過首次執行上限，不推播、
            # 不呼叫 webhook。標記為 seeded_historical，不是 'new'——'new'
            # 保留給真正待推播的項目，避免將來的 catch-up/replay 邏輯誤把
            # 這批舊資料當成待推播（見 schema.sql 的 status 欄位註解）。
            db.mark_seeded_historical(item["item_id"])
            archived_only_count += 1
            continue

        embed = build_embed(
            title=item["title"],
            description=item.get("summary") or "（無摘要，請點擊標題查看原文）",
            url=item["url"],
            footer=source_name,
            published_at=item.get("published_at"),
        )
        ok, status, err = send_webhook(webhook_url, embed)
        db.log_delivery(item["item_id"], key, status, err)
        if ok:
            db.mark_published(item["item_id"])
            pushed_count += 1
            logger.info(f"[{key}] 已推播：{item['title'][:40]}")
        else:
            logger.error(f"[{key}] 推播失敗（HTTP {status}）：{err}")

    logger.info(f"[{key}] 完成，共 {new_count} 則新項目，推播 {pushed_count} 則")

    if archived_only_count:
        logger.info(
            f"[{key}] 首次執行：已存檔 {archived_only_count} 筆歷史資料，僅推播最新 {pushed_count} 筆供驗證"
        )
        _record_summary(
            key, how,
            f"首次執行，已存檔{archived_only_count}筆歷史資料，僅推播最新{pushed_count}筆供驗證",
        )
    elif pushed_count:
        _record_summary(key, how, f"成功推播 {pushed_count} 則新項目")
    else:
        _record_summary(key, how, "無新資料（去重生效）")


def main():
    parser = argparse.ArgumentParser(description="本地爬蟲 → Discord Webhook 推播")
    parser.add_argument("--source", required=True, choices=list(SOURCE_REGISTRY.keys()) + ["all"])
    args = parser.parse_args()

    db.init_db()

    if args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
