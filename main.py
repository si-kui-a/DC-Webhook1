"""
main.py — 主執行入口。

用法：
    python main.py --source fed
    python main.py --source etf0050
    python main.py --source macro_fred
    python main.py --source twse_tsmc
    python main.py --source twse_chunghwa
    python main.py --source us_stock_digest
    python main.py --source crypto_digest
    python main.py --source macro_tech_digest
    python main.py --source geopolitics_digest
    python main.py --source tsmc_digest
    python main.py --source cbc_digest
    python main.py --source tw_stock_meta
    python main.py --source crypto_meta
    python main.py --source all

tsmc/cbc已從即時逐篇推播改為晚間彙整(見DIGEST_CHANNELS)，substack_easypoint
已併入us_stock_digest，三者都不再是SOURCE_REGISTRY的獨立--source選項。

cron 排程範例見 crontab.example。
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import truststore

truststore.inject_into_ssl()  # 修 certifi 對某些政府網站(如 cbc.gov.tw)憑證鏈驗證過嚴的問題，改用系統信任庫

from dotenv import load_dotenv

import ai_insight
import db
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050, macro_fred, twse_financials
from scrapers import substack_generic
import digest_format
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

# 獎學金來源（批次模式，共用一個 webhook URL，推播合併為一條訊息）
SCHOLARSHIP_REGISTRY = {
    "scholarship_daad": (scholarship_daad.fetch, scholarship_daad.SOURCE_NAME, scholarship_daad.SOURCE_ID),
    "scholarship_moe": (scholarship_moe.fetch, scholarship_moe.SOURCE_NAME, scholarship_moe.SOURCE_ID),
    "scholarship_thu": (scholarship_thu.fetch, scholarship_thu.SOURCE_NAME, scholarship_thu.SOURCE_ID),
    "scholarship_efg": (scholarship_efg.fetch, scholarship_efg.SOURCE_NAME, scholarship_efg.SOURCE_ID),
}

# 連續失敗超過此次數，視為需要人工介入（用於未來接外部告警，本 MVP 先只記 log）
FAIL_THRESHOLD = 3

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
                    sec_ok, sec_status, sec_err = send_webhook(secondary_url, embed)
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


# 晚間彙整頻道(甲類:AI敘事交叉比對,見設計討論)。頻道間共用同一套執行邏輯
# (run_digest_channel),只是設定不同——同一篇文章要跨頻道被不同角度消化的
# 來源(princetonchen/wublockchain123)在scrapers/substack_generic.py
# 裡用「同feed網址+不同source_id」各自獨立設定,避免共用dedup_key導致
# 先處理的頻道把去重記錄標記掉、其他頻道永遠抓不到同一篇。
#
# 美股個股/技術分析統整沿用原本「美股送分題」頻道的webhook環境變數(頻道
# 本身已在Discord改名，webhook綁的是頻道ID不是顯示名稱，不用換)。
def _normalize_tsmc(raw: dict) -> dict:
    """輕量正規化(不含summary——summary留到dedup/日期/首次執行閘門都確認
    「這筆真的要用」之後才算，避免對整批(含已抓過的歷史文章)都白白做一次
    detail頁請求+抽取式摘要運算，這是實測時cbc卡住逾7分鐘才發現的效能
    問題，tsmc同一個模式先一併修正)。"""
    return {
        "title": raw["title"],
        "url": raw["url"],
        "published_at": raw.get("published_at"),
        "source_id": tsmc.SOURCE_ID,
        "source_name": tsmc.SOURCE_NAME,
    }


def _normalize_cbc(raw: dict) -> dict:
    return {
        "title": raw["title"],
        "url": raw["url"],
        "published_at": raw.get("published_at"),
        "source_id": cbc.SOURCE_ID,
        "source_name": cbc.SOURCE_NAME,
    }


def _normalize_substack(raw: dict) -> dict:
    """substack來源的raw本身已經是完整形狀(RSS內容已含summary，不需要
    額外請求)，這裡只是統一介面，不做任何轉換。"""
    return raw


# 晚間彙整頻道(甲類:AI敘事交叉比對,見設計討論)。頻道間共用同一套執行邏輯
# (run_digest_channel)，每個頻道設定自己的fetch_fn(回傳list[dict]，每筆
# 需含title/summary/url/published_at/source_id/source_name)與source_ids
# (供首次執行閘門判斷用)。
#
# 美股個股/技術分析統整沿用原本「美股送分題」頻道的webhook環境變數(頻道
# 本身已在Discord改名，webhook綁的是頻道ID不是顯示名稱，不用換)。台積電/
# 央行新聞從原本SOURCE_REGISTRY的即時逐篇推播移到這裡的晚間彙整(見設計
# 討論)，財報/營收(twse_tsmc)維持原本獨立的事實陳述推播，兩者都會進同一個
# Discord頻道，只是各自獨立訊息，不強行合併成一則。
# 每個頻道設定：
#   fetch_fn      -> 回傳list[raw dict]（各來源原生形狀，substack是完整
#                     形狀含summary；tsmc/cbc只有title/url/published_at）
#   normalize_fn  -> raw -> {title,url,published_at,source_id,source_name}
#                     （輕量，不含summary，dedup/日期/首次執行閘門判斷只
#                     需要這些欄位，故意不算summary，避免對整批歷史文章
#                     都白白做一次detail頁請求/摘要運算）
#   summarize_fn  -> raw -> str|None（真正花運算資源的部分，只對「確認
#                     真的要用」的項目呼叫一次）
#   source_ids    -> 供首次執行閘門判斷用
DIGEST_CHANNELS = {
    "us_stock_digest": {
        "webhook_env": "WEBHOOK_ANALYST_EASYPOINT",
        "channel_title": "美股個股/技術分析統整",
        "angle": (
            "個股/類股技術面(支撐壓力、動能、財報後反應)、進出場邏輯、短線交易"
            "策略。目標是讓讀者知道今天哪些股票的技術結構出現變化，各作者的"
            "操作邏輯是什麼。"
        ),
        "fetch_fn": lambda: substack_generic.fetch_all(substack_generic.US_STOCK_FEEDS),
        "normalize_fn": _normalize_substack,
        "summarize_fn": lambda raw: raw.get("summary"),
        "source_ids": [s[0] for s in substack_generic.US_STOCK_FEEDS],
    },
    "crypto_digest": {
        "webhook_env": "WEBHOOK_CRYPTO",
        "channel_title": "加密貨幣統整",
        "angle": (
            "鏈上數據、幣價與流動性動向、DeFi/交易所動態、監管消息對幣圈操作"
            "的直接影響。目標是讓讀者知道今天幣圈發生了什麼、對持倉/操作有"
            "什麼意義。跨頻來源(如Tiger Capital Research)只抽取跟幣圈直接"
            "相關的段落，其餘(地緣政治、純總經)不列入。"
        ),
        "fetch_fn": lambda: substack_generic.fetch_all(substack_generic.CRYPTO_FEEDS),
        "normalize_fn": _normalize_substack,
        "summarize_fn": lambda raw: raw.get("summary"),
        "source_ids": [s[0] for s in substack_generic.CRYPTO_FEEDS],
    },
    "macro_tech_digest": {
        "webhook_env": "WEBHOOK_MACRO_TECH",
        "channel_title": "總經/科技趨勢統整",
        "angle": (
            "總體經濟數據解讀、科技產業(AI/雲端/半導體)的中長線結構性趨勢，"
            "偏投資組合配置的啟示，不是短線交易訊號。目標是讓讀者知道這些"
            "趨勢對整體判斷/配置有什麼啟示。跨頻來源只抽取總經數據解讀＋"
            "科技/AI產業趨勢或科技產業長線投資邏輯的部分，不含地緣政治本身、"
            "不含幣圈鏈上細節。"
        ),
        "fetch_fn": lambda: substack_generic.fetch_all(substack_generic.MACRO_TECH_FEEDS),
        "normalize_fn": _normalize_substack,
        "summarize_fn": lambda raw: raw.get("summary"),
        "source_ids": [s[0] for s in substack_generic.MACRO_TECH_FEEDS],
    },
    "geopolitics_digest": {
        "webhook_env": "WEBHOOK_GEOPOLITICS",
        "channel_title": "地緣政治/安全/時事統整",
        "angle": (
            "地緣政治事件、國安/科技管制、供應鏈安全，及其對特定產業(尤其"
            "半導體/國防/AI)與市場的衝擊路徑。目標是讓讀者知道今天有哪些"
            "地緣政治/政策事件，可能如何影響哪些產業或資產。跨頻來源只抽取"
            "地緣政治風險及其市場衝擊的部分。"
        ),
        "fetch_fn": lambda: substack_generic.fetch_all(substack_generic.GEOPOLITICS_FEEDS),
        "normalize_fn": _normalize_substack,
        "summarize_fn": lambda raw: raw.get("summary"),
        "source_ids": [s[0] for s in substack_generic.GEOPOLITICS_FEEDS],
    },
    "tsmc_digest": {
        "webhook_env": "WEBHOOK_INSTITUTIONAL_TSMC",
        "channel_title": "台積電新聞",
        "angle": (
            "台積電公司重大訊息(財報發布、法說會公告、股東會決議、購併/合作"
            "案等)。目標是讓讀者知道公司層級發生了什麼變化，不做投資建議。"
        ),
        "fetch_fn": tsmc.fetch,
        "normalize_fn": _normalize_tsmc,
        "summarize_fn": lambda raw: compute_summary("tsmc", raw),
        "source_ids": [tsmc.SOURCE_ID],
    },
    "cbc_digest": {
        "webhook_env": "WEBHOOK_INSTITUTIONAL_CBC",
        "channel_title": "央行新聞",
        "angle": (
            "台灣央行(中央銀行)政策動態、利率決議、匯率/外匯市場相關公告。"
            "目標是讓讀者知道央行今天發布了什麼、對利率/匯率政策方向有什麼"
            "含義。"
        ),
        "fetch_fn": cbc.fetch,
        "normalize_fn": _normalize_cbc,
        "summarize_fn": lambda raw: compute_summary("cbc", raw),
        "source_ids": [cbc.SOURCE_ID],
    },
}

# 首次執行安全閘門：多來源合併的digest pipeline第一次跑時，各來源的RSS
# 歷史項目加總可能上看百篇，塞進單次Gemini呼叫會逾時（實測踩到過），
# 比照FIRST_RUN_PUSH_CAP精神，只送最新N篇給AI，其餘寫入dedup_key但標記
# seeded_historical(不是真的推播過)。
DIGEST_FIRST_RUN_CAP = 15

TAIWAN_TZ = timezone(timedelta(hours=8))


def _is_today_in_taiwan(published_at: str | None) -> bool:
    """晚間彙整頻道只收錄台灣時區(UTC+8)當日發佈的文章,過往文章(哪怕是
    第一次被我們抓到、對dedup而言算「新」)不納入當日推播。RSS pubDate
    多為RFC822格式,email.utils.parsedate_to_datetime可解析。解析失敗
    (格式異常)時預設為True(視為今天)——正常RFC822格式都能正確解析,
    異常情況比較罕見,寧可誤收不要因為格式問題意外把真正的新文章擋掉。"""
    if not published_at:
        return True
    try:
        dt = parsedate_to_datetime(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tw = dt.astimezone(TAIWAN_TZ)
        today_tw = datetime.now(TAIWAN_TZ).date()
        return dt_tw.date() == today_tw
    except (TypeError, ValueError, OverflowError):
        return True


def run_digest_channel(key: str):
    """晚間彙整頻道通用執行邏輯：收集當天全部設定來源的新文章，送Gemini做
    交叉比對，整理成開頭總覽＋分類重點＋引用來源的彙整，最多拆2則訊息推播。"""
    config = DIGEST_CHANNELS[key]
    webhook_env = config["webhook_env"]
    channel_title = config["channel_title"]
    source_ids = config["source_ids"]

    if not source_ids:
        logger.info("[%s] 尚未設定任何來源，跳過", key)
        return

    webhook_url = os.getenv(webhook_env)
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, webhook_env)
        return

    # 用any()不用all()：部分來源可能已在其他頻道/舊pipeline跑過(非首次)，
    # 但只要有任何一個來源是真正第一次，這次合併起來的new_items就可能
    # 爆量，閘門就要生效——用all()會被「非首次」的來源拖累，誤判成不需要
    # 限制(實測踩過，導致大量歷史文章未經篩選全部送進Gemini)。
    is_first_run = any(db.count_items_for_source(source_id) == 0 for source_id in source_ids)

    normalize_fn = config["normalize_fn"]
    summarize_fn = config["summarize_fn"]
    raw_items = config["fetch_fn"]()

    seen_sources = set()
    new_items = []
    archived_only_count = 0
    for raw in raw_items:
        norm = normalize_fn(raw)
        source_id = norm["source_id"]
        if source_id not in seen_sources:
            db.upsert_source(source_id, norm["source_name"], "digest", norm["url"])
            seen_sources.add(source_id)

        # summary先用None插入(比照既有run_source()的模式)：dedup/日期/首次
        # 執行閘門判斷完全不需要summary，只有「真的要用」的項目才值得花運算
        # 資源算摘要(detail頁請求+抽取式摘要對tsmc/cbc是有成本的操作)。
        item = db.insert_item_if_new(
            source_id=source_id,
            title=norm["title"],
            summary=None,
            url=norm["url"],
            published_at=norm.get("published_at"),
        )
        if item is None:
            continue

        if not _is_today_in_taiwan(norm.get("published_at")):
            db.mark_stale_not_today(item["item_id"])
            continue

        if is_first_run and len(new_items) >= DIGEST_FIRST_RUN_CAP:
            db.mark_seeded_historical(item["item_id"])
            archived_only_count += 1
            continue

        summary = summarize_fn(raw) or "（無摘要，請點擊標題查看原文）"
        db.update_summary(item["item_id"], summary)
        item["summary"] = summary
        item["source_name"] = norm["source_name"]
        new_items.append(item)

    if not new_items:
        logger.info("[%s] 本次無新資料", key)
        return

    if archived_only_count:
        logger.info(
            "[%s] 首次執行：已存檔 %d 筆歷史資料，僅彙整最新 %d 筆供驗證",
            key, archived_only_count, len(new_items),
        )

    digest = ai_insight.build_channel_digest(config["angle"], new_items)
    if not digest:
        logger.error("[%s] Gemini彙整失敗（額度用盡/網路錯誤/回應格式不對），本次略過推播", key)
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    embeds, omitted_count = digest_format.build_digest_embeds(digest, channel_title, date_str)

    all_ok = True
    for i, embed in enumerate(embeds):
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 彙整推播成功（訊息 %d/%d）", key, i + 1, len(embeds))
        else:
            all_ok = False
            logger.error("[%s] 彙整推播失敗（訊息 %d/%d）：HTTP %s %s", key, i + 1, len(embeds), status, err)

    # 收錄進訊息的重點對應到的items一律標published；篇幅省略的另外標記，
    # 兩者都不是「還沒處理過」，避免dedup之外還被誤判成待推播。
    # 這裡簡化處理：只要AI彙整跟推播本身成功，當次收集到的new_items全部視為
    # 已處理(published)；細緻到「哪個item對應到哪個point被省略」需要
    # ai_insight回傳時保留item_id關聯，暫用簡化版。
    if all_ok:
        for item in new_items:
            db.mark_published(item["item_id"])
        logger.info(
            "[%s] 完成，共 %d 則新項目，彙整成 %d 則訊息%s",
            key, len(new_items), len(embeds),
            f"（另有{omitted_count}則重點因篇幅省略）" if omitted_count else "",
        )

        # 存檔這次彙整的完整文字(獨立的digest_report.*命名空間,不影響原本
        # 文章來源的dedup)，供大總結頻道(run_meta_summary_channel)之後
        # 讀取當天各頻道已產出的內容——AI敘事彙整頻道原本產出後只推播、
        # 沒有存檔，這是新增的持久化機制。
        report_text = digest["overview"] + "\n\n" + "\n".join(
            f"【{p['category']}】{p['point']}" for p in digest["points"]
        )
        report_source_id = f"digest_report.{key}"
        db.upsert_source(report_source_id, f"{channel_title}彙整存檔", "digest_report", "")
        saved = db.insert_item_if_new(
            source_id=report_source_id,
            title=f"{channel_title} 彙整（{date_str}）",
            summary=report_text,
            url=webhook_url,
            published_at=date_str,
        )
        if saved:
            db.mark_published(saved["item_id"])


# 大總結頻道(乙類→其實是丙類:彙整「其餘頻道已產出的內容」而非原始新聞,
# 需要AI綜合研判進出場/情緒判斷,見設計討論使用者確認)。
# key -> (讀取用source_id, 顯示名稱)。digest_report.*是run_digest_channel()
# 存檔的彙整文字；fred.macro_indicators/stockintelli.tracking是既有
# SOURCE_REGISTRY來源，本來就把完整報告存在item.summary，直接沿用不用
# 額外存檔。twse_tsmc/twse_chunghwa/中華電財報(一天可能兩筆不同標題)
# 暫不納入，財報季頻資料對「今天該不該進出場」的即時判斷幫助有限，
# 之後真的需要可再擴充。
CONTRIBUTING_CHANNELS = {
    "us_stock": ("digest_report.us_stock_digest", "美股個股/技術分析統整"),
    "crypto": ("digest_report.crypto_digest", "加密貨幣統整"),
    "macro_tech": ("digest_report.macro_tech_digest", "總經/科技趨勢統整"),
    "geopolitics": ("digest_report.geopolitics_digest", "地緣政治/安全/時事統整"),
    "tsmc": ("digest_report.tsmc_digest", "台積電新聞"),
    "cbc": ("digest_report.cbc_digest", "央行新聞"),
    "macro_fred": ("fred.macro_indicators", "總經指標追蹤"),
    "etf0050": ("stockintelli.tracking", "台股權值股追蹤"),
}

META_SUMMARY_CHANNELS = {
    "tw_stock_meta": {
        "webhook_env": "WEBHOOK_TW_STOCK_META",
        "channel_title": "台股大總結",
        "angle": (
            "綜合研判台股各標的現在是否應進出場、市場情況/情緒(樂觀/悲觀)、"
            "理性面(數據)與實際面(市場反應)是否一致、產業與法人動向，"
            "美股對台股的傳導影響也要納入考量。"
        ),
        "sources": ["etf0050", "tsmc", "cbc", "macro_fred", "macro_tech", "geopolitics", "us_stock"],
    },
    "crypto_meta": {
        "webhook_env": "WEBHOOK_CRYPTO_META",
        "channel_title": "幣圈大總結",
        "angle": (
            "綜合研判幣圈各標的現在是否應進出場、市場情況/情緒(樂觀/悲觀)、"
            "理性面(數據)與實際面(市場反應)是否一致、總經流動性對幣圈的"
            "傳導影響。"
        ),
        "sources": ["crypto", "macro_fred", "macro_tech", "geopolitics"],
    },
}


def run_meta_summary_channel(key: str):
    """大總結頻道：讀取各貢獻頻道當天已產出的報告(不重新抓原始新聞)，
    送Gemini綜合研判進出場/情緒，最多拆2則訊息推播。任一貢獻頻道今天
    沒有報告就跳過該來源，不是整個大總結失敗。"""
    config = META_SUMMARY_CHANNELS[key]
    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    reports = []
    for src_key in config["sources"]:
        source_id, channel_name = CONTRIBUTING_CHANNELS[src_key]
        text = db.get_summary_for_date(source_id, today_str)
        if text:
            reports.append({"channel_name": channel_name, "report_text": text})

    if not reports:
        logger.info("[%s] 今日尚無任何來源頻道報告可供彙整，跳過", key)
        return

    summary = ai_insight.build_meta_summary(config["angle"], reports)
    if not summary:
        logger.error("[%s] Gemini大總結彙整失敗（額度用盡/網路錯誤/回應格式不對），本次略過推播", key)
        return

    embeds, omitted_count = digest_format.build_digest_embeds(summary, config["channel_title"], today_str)
    all_ok = True
    for i, embed in enumerate(embeds):
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 大總結推播成功（訊息 %d/%d）", key, i + 1, len(embeds))
        else:
            all_ok = False
            logger.error("[%s] 大總結推播失敗（訊息 %d/%d）：HTTP %s %s", key, i + 1, len(embeds), status, err)

    if all_ok:
        logger.info(
            "[%s] 完成，彙整 %d 個來源頻道，共 %d 則訊息%s",
            key, len(reports), len(embeds),
            f"（另有{omitted_count}則重點因篇幅省略）" if omitted_count else "",
        )


def main():
    parser = argparse.ArgumentParser(description="本地爬蟲 → Discord Webhook 推播")
    parser.add_argument("--source", choices=list(SOURCE_REGISTRY.keys()) + ["all"]
                                     + list(DIGEST_CHANNELS.keys()) + list(META_SUMMARY_CHANNELS.keys()),
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
    elif args.source in DIGEST_CHANNELS:
        run_digest_channel(args.source)
    elif args.source in META_SUMMARY_CHANNELS:
        run_meta_summary_channel(args.source)
    elif args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
