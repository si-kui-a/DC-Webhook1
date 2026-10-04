"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import logging
import os

import ai_insight
import db
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, etf0050, macro_fred, twse_financials
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from jobs.paths import SOURCE_HEALTH_PATH

logger = logging.getLogger("main")

# source_id -> (fetch函式, 對應Webhook環境變數名稱, 顯示名稱)
# tsmc/cbc/substack_easypoint已移出(改走DIGEST_CHANNELS的晚間彙整,見設計
# 討論)，這裡只留「維持即時逐篇推播」的來源，跟純事實陳述(財報/營收)。
SOURCE_REGISTRY = {
    "fed": (fed.fetch, "WEBHOOK_INSTITUTIONAL_FED", fed.SOURCE_NAME, fed.SOURCE_ID),
    "etf0050": (etf0050.fetch, "WEBHOOK_INSTITUTIONAL_0050", etf0050.SOURCE_NAME, etf0050.SOURCE_ID),
    "macro_fred": (macro_fred.fetch, "WEBHOOK_INSTITUTIONAL_MACRO", macro_fred.SOURCE_NAME, macro_fred.SOURCE_ID),
    # 財報/營收為純事實陳述(TWSE官方開放資料,非AI敘事生成),tsmc這支併入既有
    # 台積電新聞頻道(同公司同頻道),中華電是全新公司,獨立開一個頻道。
    "twse_tsmc": (twse_financials.fetch_tsmc, "WEBHOOK_INSTITUTIONAL_TSMC",
                  twse_financials.SOURCE_NAME_TSMC, twse_financials.SOURCE_ID_TSMC),
    "twse_chunghwa": (twse_financials.fetch_chunghwa, "WEBHOOK_CHUNGHWA",
                      twse_financials.SOURCE_NAME_CHUNGHWA, twse_financials.SOURCE_ID_CHUNGHWA),
}
# 連續失敗超過此次數，視為需要人工介入（用於未來接外部告警，本 MVP 先只記 log）
FAIL_THRESHOLD = 3
# fed/tsmc英文新聞翻譯上限(2026-07-31使用者確認,見docs/gemini_quota_allocation.md)：
# 單次執行最多翻譯前N則最新文章,避免一次抓到多篇新文章時把當日Gemini配額
# 一口氣用完,超過上限的退回英文摘要(不是錯誤,是設計上的降級)。
TRANSLATION_CAP_PER_RUN = 3
# 首次執行安全閘門：fed 本來就是個位數筆數，沒有這個風險，故不列在這裡
# （值為 None 代表不套用閘門）。cbc/substack_easypoint已移到DIGEST_CHANNELS
# (見DIGEST_FIRST_RUN_CAP，同樣邏輯的閘門)，這裡不再需要。
FIRST_RUN_PUSH_CAP = {}

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
        record_source_health(SOURCE_HEALTH_PATH, source_id, "FAILED", error=str(e))
        logger.error(f"[{key}] 抓取失敗（累計 {fail_count} 次）：{e}", exc_info=True)
        if fail_count >= FAIL_THRESHOLD:
            logger.critical(f"[{key}] 已連續失敗 {fail_count} 次，需人工檢查 selector 是否因改版失效")
        _record_summary(key, how, f"例外失敗（累計{fail_count}次），完整 traceback 見 work/error.log")
        return

    report = validate_items(raw_items)
    raw_items = report.accepted
    if report.rejected:
        logger.warning(f"[{key}] PARTIAL: rejected={report.rejected} errors={report.errors[:3]}")
        _record_summary(key, how, f"PARTIAL rejected={report.rejected}")
    health_status = "PARTIAL" if report.rejected else ("EMPTY_VALID" if not raw_items else "SUCCESS")
    record_source_health(SOURCE_HEALTH_PATH, source_id, health_status, item_count=len(raw_items), rejected_count=report.rejected)
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
    translation_count = 0
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
        # 2026-07-31新增上限(使用者確認):單次執行最多翻譯前TRANSLATION_CAP
        # 則最新文章,避免單次執行(尤其新項目一次湧入時)把當日Gemini配額
        # 一口氣用完,超過上限的一律退回英文摘要(跟Gemini呼叫失敗同一個
        # 降級路徑,不是新的錯誤狀態)。
        sentiment = None
        sentiment_reason = None
        if summary and key in DETAIL_FETCHERS and translation_count < TRANSLATION_CAP_PER_RUN:
            insight = ai_insight.get_translation_and_sentiment(summary)
            translation_count += 1
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
            # 同步推送到 Telegram（與 finfeed 共用 bot token）——預設一律推送
            # (.get預設True,向下相容既有來源,它們的raw沒有telegram_alert這個
            # key)；macro_fred會依閥值判斷結果明確設True/False,只有觸發閥值
            # 才同步Telegram,避免每日固定推送的Discord訊息連帶洗版Telegram。
            if raw.get("telegram_alert", True):
                # 部分來源(如etf0050)Telegram只想推「重點/警示」而非完整
                # Discord報告內容(完整版本篇幅太長，Telegram該是快速通知
                # 不是取代Discord)，用raw裡的telegram_summary覆蓋預設的
                # 完整summary；沒設這個欄位的既有來源(fed/scholarship等)
                # 行為不變,繼續用完整summary。
                tg_text = notify_telegram.build_message(
                    title=item["title"],
                    body=raw.get("telegram_summary") or item.get("summary") or "（無摘要）",
                    sentiment=sentiment,
                    sentiment_reason=sentiment_reason,
                    url=item["url"],
                )
                notify_telegram.send_message(tg_text)

            # 額外推播到獨立警報頻道(目前只有macro_fred的異常閾值觸發時
            # 會設這個欄位，見scrapers/macro_fred.py)，跟每日固定報告的
            # 頻道分開，避免警報被例行內容稀釋，不影響主要webhook的推播
            # 結果判定(失敗只記log，不算這筆item失敗)。
            secondary_env = raw.get("secondary_webhook_env")
            if secondary_env:
                secondary_url = os.getenv(secondary_env)
                if secondary_url:
                    # secondary_summary存在時(2026-07-31使用者確認)，警報
                    # 頻道只顯示觸發閥值的內容，不是主頻道那份完整報告——
                    # 另外建一個embed，不影響主頻道已經送出的embed。
                    secondary_summary = raw.get("secondary_summary")
                    secondary_embed = embed if not secondary_summary else build_embed(
                        title=item["title"],
                        description=secondary_summary,
                        url=item["url"],
                        footer=source_name,
                        published_at=item.get("published_at"),
                        fields=fields,
                        color=color,
                    )
                    sec_ok, sec_status, sec_err = send_webhook(secondary_url, secondary_embed)
                    if sec_ok:
                        logger.info(f"[{key}] 額外推播到警報頻道成功")
                    else:
                        logger.error(f"[{key}] 額外推播到警報頻道失敗（HTTP {sec_status}）：{sec_err}")
                else:
                    logger.error(f"[{key}] 缺少警報頻道環境變數 {secondary_env}")
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
