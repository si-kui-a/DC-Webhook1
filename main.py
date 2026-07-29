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
from datetime import datetime

import truststore

truststore.inject_into_ssl()  # 修 certifi 對某些政府網站(如 cbc.gov.tw)憑證鏈驗證過嚴的問題，改用系統信任庫

from dotenv import load_dotenv

import ai_insight
import db
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util

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

# 獎學金來源（批次模式，共用一個 webhook URL，推播合併為一條訊息）
SCHOLARSHIP_REGISTRY = {
    "scholarship_daad": (scholarship_daad.fetch, scholarship_daad.SOURCE_NAME, scholarship_daad.SOURCE_ID),
    "scholarship_moe": (scholarship_moe.fetch, scholarship_moe.SOURCE_NAME, scholarship_moe.SOURCE_ID),
    "scholarship_thu": (scholarship_thu.fetch, scholarship_thu.SOURCE_NAME, scholarship_thu.SOURCE_ID),
    "scholarship_efg": (scholarship_efg.fetch, scholarship_efg.SOURCE_NAME, scholarship_efg.SOURCE_ID),
}

# 連續失敗超過此次數，視為需要人工介入（用於未來接外部告警，本 MVP 先只記 log）
FAIL_THRESHOLD = 3

# 首次執行安全閘門：只套用在 cbc（RSS 一次回傳全部歷史，curl 實測約500筆，
# 資料庫是空的時候不能全部當「新項目」推播，否則洗版）。fed/tsmc 本來就是
# 個位數筆數，沒有這個風險，故不列在這裡（值為 None 代表不套用閘門）。
FIRST_RUN_PUSH_CAP = {
    "cbc": 5,
}

# 詳情頁抓取函式，只有 fed/tsmc 需要（cbc 直接用 RSS description，
# stockintelli (etf0050) 改為 RSC payload 嵌入式資料，不需要詳情頁）。
DETAIL_FETCHERS = {
    "fed": fed.fetch_detail_text,
    "tsmc": tsmc.fetch_detail_text,
}


def compute_summary(key: str, raw: dict) -> str | None:
    """依來源語言/資料型態選擇對應摘要策略。任何一步失敗（detail 頁
    403/超時、selector 抓空、摘要套件例外）都回傳 None，讓呼叫端沿用
    「無摘要」的既有邏輯，不能讓單一 item 的摘要失敗中斷整個來源的
    推播流程。"""
    try:
        if key == "cbc":
            raw_description = raw.get("raw_description")
            return summarizer_zh.summarize(raw_description, title=raw.get("title")) if raw_description else None

        fetch_detail = DETAIL_FETCHERS.get(key)
        if fetch_detail:
            detail_text = fetch_detail(raw["url"])
            return summarizer_en.summarize(detail_text)
    except Exception as e:
        logger.warning(f"[{key}] 摘要產生失敗（{raw.get('url')}）：{e}")
        return None

    return None


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

        # 只對「真的會被推播」的項目算摘要——首次執行安全閘門擋下的歷史
        # 項目不需要，省下 fed/tsmc 額外的 detail 頁請求與 cbc 的 TextRank
        # 運算。summary 一開始是 None（insert 時就是這樣），這裡算出來後
        # 才補寫回 db，讓 db 裡存的內容跟實際推播出去的一致。
        summary = compute_summary(key, raw)

        # fed/tsmc(英文來源)才需要中文翻譯+利多利空判斷,cbc本來就是中文。
        # 用Gemini免費層把既有抽取式摘要(已經是LexRank挑出的重點句,不重新
        # 抓detail頁)翻譯+分類;Gemini失敗(額度用盡/網路錯誤/未設定key)
        # 一律靜默退回原本的英文抽取式摘要,不影響推播本身。sentiment不寫
        # 進db——它是每次推播當下的輔助判斷,不算「這則新聞的固定摘要」,
        # 不影響dedup/db一致性原則。
        sentiment = None
        sentiment_reason = None
        if summary and key in DETAIL_FETCHERS:
            insight = ai_insight.get_translation_and_sentiment(summary)
            if insight:
                summary = insight["zh_summary"]
                sentiment = insight["sentiment"]
                sentiment_reason = insight["sentiment_reason"]

        if summary:
            db.update_summary(item["item_id"], summary)
            item["summary"] = summary

        fields = None
        color = 5793266
        if sentiment:
            fields = [{"name": f"AI 情緒判斷：{sentiment}", "value": sentiment_reason or "（無理由）"}]
            color = 15105570  # 橙色,呼應push_webhook.py既有「推論性內容」色碼慣例

        embed = build_embed(
            title=item["title"],
            description=item.get("summary") or "（無摘要，請點擊標題查看原文）",
            url=item["url"],
            footer=source_name,
            published_at=item.get("published_at"),
            fields=fields,
            color=color,
        )
        ok, status, err = send_webhook(webhook_url, embed)
        db.log_delivery(item["item_id"], key, status, err)
        if ok:
            db.mark_published(item["item_id"])
            pushed_count += 1
            logger.info(f"[{key}] 已推播：{item['title'][:40]}")
            # 同步推送到 Telegram（與 finfeed 共用 bot token）
            sentiment_line = f"\nAI 情緒判斷：{sentiment}（{sentiment_reason}）\n" if sentiment else ""
            tg_text = (
                f"*{item['title']}*\n"
                f"{item.get('summary') or '（無摘要）'}\n"
                f"{sentiment_line}\n"
                f"🔗 {item['url']}"
            )
            notify_telegram.send_message(tg_text)
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
            logger.error("[%s] 抓取失敗（累計 %d 次）: %s", key, fail_count, e)
            continue

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
            logger.error("獎學金批次推播失敗（chunk %d/%d）: HTTP %s %s", i + 1, len(chunks), status, err)

    # Telegram — 僅簡短通知，不再在頻道內發佈完整內容；完整獎學金全數統一於
    # Discord 機器人完整輸出（見上方 webhook 推播）。
    source_summary = "、".join(
        f"{name}（{len(items)} 筆）"
        for name, items in new_items_by_source.items()
        if items
    )
    tg_brief = (
        f"📚 *獎學金快報已更新*\n"
        f"共 {total_new} 筆新項目\n"
        f"來源：{source_summary}\n\n"
        f"完整內容請至 Discord #獎學金頻道查看"
    )
    notify_telegram.send_message(tg_brief)


def main():
    parser = argparse.ArgumentParser(description="本地爬蟲 → Discord Webhook 推播")
    parser.add_argument("--source", choices=list(SOURCE_REGISTRY.keys()) + ["all"],
                        help="執行單一來源（與 --scholarship 二選一）")
    parser.add_argument("--scholarship", action="store_true",
                        help="批次執行所有獎學金來源")
    args = parser.parse_args()

    if not args.source and not args.scholarship:
        parser.print_help()
        sys.exit(1)
    if args.source and args.scholarship:
        parser.error("--source 與 --scholarship 不能同時使用")

    db.init_db()

    if args.scholarship:
        run_scholarship()
    elif args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
