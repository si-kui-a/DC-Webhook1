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
    python main.py --source semi_supply_chain_digest
    python main.py --source tw_stock_meta
    python main.py --source crypto_meta
    python main.py --source daily_recap
    python main.py --source all

tsmc/cbc已從即時逐篇推播改為晚間彙整(見DIGEST_CHANNELS)，substack_easypoint
已併入us_stock_digest，三者都不再是SOURCE_REGISTRY的獨立--source選項。

排程用Windows工作排程器，非cron，見scripts/setup_scheduled_tasks.ps1。
"""
import argparse
import json
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
import price_feed
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050, macro_fred, twse_financials
from scrapers import substack_generic
from scrapers import semi_supply_chain
import digest_format
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util
from scrapers import internship_mol
from scrapers import internship_104
from scrapers import internship_518
from scrapers import internship_rich
from scrapers import internship_yes123
from scrapers import internship_util

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

# 台灣實習頻道(比照獎學金頻道模式)。科系/資歷/學校/年級/國籍/身份別排除
# 規則已套用(2026-07-31，見internship_util.is_relevant())。104用關鍵字
# "實習"直接呼叫官方站內搜尋API(見internship_104.py)，命中率比MOL的
# 全量快照+本地篩選高很多(實測約77% vs 1%)，因為104自己的搜尋引擎已經
# 先做過一次相關性排序。518(見internship_518.py)無CAPTCHA但實測偏服務業/
# 兼職，同一天的「實習」搜尋結果實測命中率0%(遠低於104)，先備而不用，
# 不會主動洗版(0筆新項目不會推播)。1111人力銀行有主動的CAPTCHA/反爬蟲
# 挑戰機制(altcha widget)，明確不做(見2026-07-31對話紀錄的界線說明)。
#
# registry值的第4個欄位skip_keyword_gate：RICH(教育部青年署見習/工讀
# 平台，見internship_rich.py)整體只有十幾筆職缺，且官方用語是「見習/
# 工讀」不是「實習」，用「實習」關鍵字計分門檻會把整個來源擋光——這個
# 來源改用internship_util.passes_profile_filters()，跳過關鍵字門檻，
# 只套用學校/年級/國籍/身份別/科系/行業別排除規則。
INTERNSHIP_REGISTRY = {
    "internship_mol": (internship_mol.fetch, internship_mol.SOURCE_NAME, internship_mol.SOURCE_ID, False),
    "internship_104": (internship_104.fetch, internship_104.SOURCE_NAME, internship_104.SOURCE_ID, False),
    "internship_518": (internship_518.fetch, internship_518.SOURCE_NAME, internship_518.SOURCE_ID, False),
    "internship_rich": (internship_rich.fetch, internship_rich.SOURCE_NAME, internship_rich.SOURCE_ID, True),
    "internship_yes123": (internship_yes123.fetch, internship_yes123.SOURCE_NAME, internship_yes123.SOURCE_ID, False),
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

INTERNSHIP_WEBHOOK_ENV = "WEBHOOK_INTERNSHIP"
INTERNSHIP_FIRST_RUN_CAP = 20


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
    # 獎學金屬於教育類內容，改用獨立的Schule mithelfer bot，不跟財經類的
    # finfeed bot共用(使用者確認2026-07-30)。
    notify_telegram.send_message(
        tg_brief,
        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID,
    )


def _build_internship_batch(items_by_source: dict[str, list[dict]]) -> list[str]:
    """比照_build_scholarship_batch()的組裝邏輯，只是文案改成實習。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    header = f"💼 *台灣實習快報* | {date_str}"
    full_lines = [header, ""]

    for source_name, items in items_by_source.items():
        if not items:
            continue
        full_lines.append("━━━━━━━━━━━━━")
        full_lines.append(f"*【{source_name}】*（{len(items)} 筆）")
        for item in items:
            title = item["title"][:120]
            url = item.get("url", "")
            full_lines.append(f"• [{title}]({url})" if url else f"• {title}")

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
            title = item["title"][:120]
            url = item.get("url", "")
            line = f"• [{title}]({url})" if url else f"• {title}"
            if len("\n".join(current + [line])) > 3900:
                chunks.append("\n".join(current))
                current = [f"💼 *台灣實習快報*（續）| {date_str}", "", line]
            else:
                current.append(line)
    if len(current) > 2:
        chunks.append("\n".join(current))
    return chunks


def run_internship():
    """台灣實習頻道：比照run_scholarship()的批次執行模式。全部篩選(關鍵字
    計分+學校/年級/國籍/身份別/科系/行業/語意噪音)都在internship_util.
    is_relevant()裡規則式完成，零AI依賴(2026-07-31移除原本的Gemini語意
    消歧步驟，見internship_util.py模組docstring)。"""
    webhook_url = os.getenv(INTERNSHIP_WEBHOOK_ENV)
    if not webhook_url:
        logger.error("缺少環境變數 %s，跳過實習批次", INTERNSHIP_WEBHOOK_ENV)
        return

    internship_util.invalidate_cache()
    is_first_run = all(db.count_items_for_source(info[2]) == 0 for info in INTERNSHIP_REGISTRY.values())

    new_items_by_source: dict[str, list[dict]] = {}
    total_new = 0

    for key, (fetch_fn, source_name, source_id, skip_keyword_gate) in INTERNSHIP_REGISTRY.items():
        db.upsert_source(source_id, source_name, "internship", "")
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
        push_cap = INTERNSHIP_FIRST_RUN_CAP if is_first_run else None
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

            filter_text = raw.get("_filter_text") or raw["title"]
            if skip_keyword_gate:
                relevant = internship_util.passes_profile_filters(filter_text)
            else:
                relevant = internship_util.is_relevant(filter_text, salary=raw.get("_salary_high"))
            if not relevant:
                db.mark_seeded_historical(item["item_id"])
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
        logger.info("實習批次完成，無新項目")
        return

    chunks = _build_internship_batch(new_items_by_source)
    if not chunks:
        logger.warning("實習批次文字組裝失敗（可能為空）")
        return

    all_ok = True
    for i, chunk in enumerate(chunks):
        embed = build_embed(
            title=f"💼 台灣實習快報{'（續）' if i > 0 else ''}",
            description=chunk,
            url="",
            footer="台灣實習監控",
        )
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("實習批次推播成功（chunk %d/%d）", i + 1, len(chunks))
        else:
            all_ok = False
            logger.error("實習批次推播失敗（chunk %d/%d）: HTTP %s %s", i + 1, len(chunks), status, err)

    # 標記已推播(同run_scholarship()的PAT-03修正，2026-07-30發現兩處都漏了)。
    if all_ok:
        for items in new_items_by_source.values():
            for item in items:
                db.mark_published(item["item_id"])

    # 比照run_scholarship()的Telegram簡短通知模式(使用者確認2026-07-30
    # 加上)，同樣用Schule mithelfer bot(教育類，跟財經的finfeed bot分開)。
    source_summary = "、".join(
        f"{name}（{len(items)} 筆）"
        for name, items in new_items_by_source.items()
        if items
    )
    tg_brief = (
        f"💼 *台灣實習快報已更新*\n"
        f"共 {total_new} 筆新項目\n"
        f"來源：{source_summary}\n\n"
        f"完整內容請至 Discord #台灣實習情報查看"
    )
    notify_telegram.send_message(
        tg_brief,
        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID,
    )


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


def _normalize_semi_supply_chain(raw: dict) -> dict:
    """台廠(semi_tw_suppliers)/美股客戶(us_customer_feeds)兩個來源的raw
    形狀本身已一致含source_id/source_name(建置時就統一過)，不需轉換，
    比照_normalize_substack。"""
    return raw


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
    "semi_supply_chain_digest": {
        "webhook_env": "WEBHOOK_SEMI_SUPPLY_CHAIN",
        "channel_title": "半導體供應鏈",
        "angle": (
            "台積電上下游台廠供應鏈(封測/設備/材料)公司重大訊息，以及美股"
            "主要客戶(Apple/NVIDIA/AMD/Broadcom)跟晶片產能/代工/客製晶片/"
            "台灣生態系投資直接相關的新聞(已用關鍵字過濾掉一般企業新聞，"
            "見us_customer_util.py)。目標是讓讀者知道供應鏈上下游今天有"
            "哪些產能、訂單、投資動態，不做投資建議。"
        ),
        "fetch_fn": semi_supply_chain.fetch,
        "normalize_fn": _normalize_semi_supply_chain,
        "summarize_fn": lambda raw: None,
        "source_ids": semi_supply_chain.SOURCE_IDS,
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
    # 2026-07-31修正：原本沒有try/except，跟run_source()的既有容錯模式不
    # 一致。單一來源的來源自己就整合了多個substack_generic feed，任一個
    # feed解析失敗都可能讓fetch_fn()丟出例外，若不接住，--digest-all批次
    # 迴圈裡這個例外會直接中斷、後面的頻道完全不會執行——這在改成批次
    # flag之前不是問題(每個頻道是獨立Windows Task，互不影響)，是這次
    # 排程整併新引入的風險，這裡補上避免退化。
    try:
        raw_items = config["fetch_fn"]()
    except Exception as e:
        logger.error(f"[{key}] 抓取失敗：{e}", exc_info=True)
        return

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

        # 存檔這次大總結的完整文字,供模擬持倉頻道(run_portfolio_channel)
        # 之後讀取「今天(或最近一次)的大總結研判內容」——比照
        # run_digest_channel()對digest_report.*的既有持久化模式。
        report_text = summary["overview"] + "\n\n" + "\n".join(
            f"【{p['category']}】{p['point']}" for p in summary["points"]
        )
        report_source_id = f"digest_report.{key}"
        db.upsert_source(report_source_id, f"{config['channel_title']}彙整存檔", "digest_report", "")
        saved = db.insert_item_if_new(
            source_id=report_source_id,
            title=f"{config['channel_title']} 彙整（{today_str}）",
            summary=report_text,
            url=webhook_url,
            published_at=today_str,
        )
        if saved:
            db.mark_published(saved["item_id"])


# 每日晨間快報(使用者2026-07-31確認)：早上7:00推播「昨日全日」跨頻道
# 重點摘要，範圍刻意選已經是最上層合成結果的3個來源(tw_stock_meta/
# crypto_meta本身就是彙整過etf0050/tsmc/cbc/macro_fred/macro_tech/
# geopolitics/us_stock/crypto的大總結；semi_supply_chain_digest不在
# 任一大總結的contributing sources裡，故額外納入)，不是重新彙整所有
# 原始頻道——避免跟tw_stock_meta/crypto_meta的內容大量重複，也維持
# prompt大小可控(低成本)。同時推播Discord(新頻道)+Telegram(沿用finfeed
# bot，使用者確認"日報週報在DC應另開頻道"、Telegram沿用既有bot不用
# 新申請)。
DAILY_RECAP_SOURCES = [
    ("digest_report.tw_stock_meta", "台股大總結"),
    ("digest_report.crypto_meta", "幣圈大總結"),
    ("digest_report.semi_supply_chain_digest", "半導體供應鏈"),
]


def run_daily_recap():
    """每日晨間快報：讀取昨天(不是今天——這是早上7點跑的T+1晨報，各
    大總結頻道是前一晚才產出報告)已產出的報告，送Gemini壓縮成「只提供
    明確重點」的精簡摘要，推播到Discord新頻道+Telegram(finfeed bot)。"""
    key = "daily_recap"
    webhook_url = os.getenv("WEBHOOK_DAILY_RECAP")
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 WEBHOOK_DAILY_RECAP，跳過", key)
        return

    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    reports = []
    for source_id, channel_name in DAILY_RECAP_SOURCES:
        text = db.get_summary_for_date(source_id, yesterday_str)
        if text:
            reports.append({"channel_name": channel_name, "report_text": text})

    if not reports:
        logger.info("[%s] 昨日(%s)尚無任何來源頻道報告可供彙整，跳過", key, yesterday_str)
        return

    angle = (
        "把過去一天的重點濃縮成明確、精簡的摘要，不做投資建議、不需要"
        "詳盡的分析過程，讀者要能在幾秒內抓到「昨天發生了什麼重要的事」。"
    )
    summary = ai_insight.build_meta_summary(angle, reports)
    if not summary:
        logger.error("[%s] Gemini彙整失敗（額度用盡/網路錯誤/回應格式不對），本次略過推播", key)
        return

    channel_title = "每日晨間快報"
    embeds, omitted_count = digest_format.build_digest_embeds(summary, channel_title, yesterday_str)
    all_ok = True
    for i, embed in enumerate(embeds):
        ok, status, err = send_webhook(webhook_url, embed)
        if ok:
            logger.info("[%s] 推播成功（訊息 %d/%d）", key, i + 1, len(embeds))
        else:
            all_ok = False
            logger.error("[%s] 推播失敗（訊息 %d/%d）：HTTP %s %s", key, i + 1, len(embeds), status, err)

    tg_points = "\n".join(f"• 【{p['category']}】{p['point']}" for p in summary["points"])
    tg_text = (
        f"☀️ *每日晨間快報（{yesterday_str}）*\n\n"
        f"{summary['overview']}\n\n{tg_points}\n\n"
        f"完整內容請至 Discord #每日晨間快報 查看"
    )
    notify_telegram.send_message(tg_text)

    if all_ok:
        logger.info(
            "[%s] 完成，彙整 %d 個來源，共 %d 則Discord訊息%s",
            key, len(reports), len(embeds),
            f"（另有{omitted_count}則重點因篇幅省略）" if omitted_count else "",
        )


# 模擬持倉(紙上帳戶,使用者確認2026-07-30,見schema.sql同段落註解)：每個
# 帳戶讀取對應大總結頻道「最近幾次」已產出的報告(不限定當天——tw_stock
# 於台股收盤後13:30左右執行,當天晚上20:30才會有tw_stock_meta的新報告,
# 收盤時點只有前一晚的報告可用,見db.get_recent_summaries()；PAT-15
# enrichment之後改用這個而非單筆的get_latest_summary())，交給AI決定
# 進出場，全部是模擬交易，不動用真實資金。3個帳戶各自獨立頻道(使用者
# 確認2026-07-30)，不共用webhook——避免3個帳戶的動作/持倉訊息混在同一個
# 頻道裡難以分辨。
PORTFOLIO_CHANNELS = {
    "tw_stock_portfolio": {
        "portfolio_id": "tw_stock",
        "webhook_env": "WEBHOOK_PORTFOLIO_TW_STOCK",
        "channel_title": "模擬持倉－台股",
        "meta_source_id": "digest_report.tw_stock_meta",
        "angle": "台股現貨帳戶,只能做多(side必須是long),leverage固定為1,不可放空。",
    },
    "crypto_futures_portfolio": {
        "portfolio_id": "crypto_futures",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_FUTURES",
        "channel_title": "模擬持倉－幣圈合約",
        "meta_source_id": "digest_report.crypto_meta",
        "angle": "幣圈合約帳戶,可做多可做空(side可為long或short),可使用槓桿"
                 "(leverage可大於1,但務必評估清算風險,不要無節制放大槓桿)。",
    },
    "crypto_discretionary_portfolio": {
        "portfolio_id": "crypto_discretionary",
        "webhook_env": "WEBHOOK_PORTFOLIO_CRYPTO_DISCRETIONARY",
        "channel_title": "模擬持倉－幣圈自主判斷",
        "meta_source_id": "digest_report.crypto_meta",
        "angle": "幣圈現貨帳戶,只能做多(side必須是long),leverage固定為1,不可放空、不可用槓桿。",
    },
}


def _price_with_pnl(position: dict) -> dict | None:
    """幫position補上current_price/unrealized_pnl/market_value,查價失敗
    回傳None,呼叫端須整批放棄本次執行——缺價無法正確算PnL,不可用avg_cost
    或0頂替(會讓數字失真,違反PnL公式須先核對的規則)。"""
    price = price_feed.get_price(position["symbol"])
    if price is None:
        return None
    margin_used = position["avg_cost"] * position["quantity"] / position["leverage"]
    if position["side"] == "short":
        pnl = (position["avg_cost"] - price) * position["quantity"]
    else:
        pnl = (price - position["avg_cost"]) * position["quantity"]
    return {
        **position,
        "current_price": price,
        "unrealized_pnl": pnl,
        "market_value": margin_used + pnl,
    }


def run_portfolio_channel(key: str):
    """模擬持倉頻道：讀取對應大總結報告+目前持倉現價，交給AI決定進出場，
    實際執行(寫db.trade_log/position/portfolio.current_cash)後推播今日
    動作+目前持倉摘要。任一步驟失敗(缺webhook/缺報告/查價失敗/Gemini失敗)
    一律整批跳過，不半套執行——避免「AI決定要交易但價格查不到」這種
    半吊子狀態寫進trade_log。"""
    config = PORTFOLIO_CHANNELS[key]
    portfolio_id = config["portfolio_id"]

    webhook_url = os.getenv(config["webhook_env"])
    if not webhook_url:
        logger.error("[%s] 缺少環境變數 %s，跳過", key, config["webhook_env"])
        return

    db.init_portfolios()
    portfolio = db.get_portfolio(portfolio_id)
    recent_reports = db.get_recent_summaries(config["meta_source_id"], limit=5)
    if not recent_reports:
        logger.info("[%s] 尚無可用的大總結報告，跳過", key)
        return
    recent_trades = db.get_recent_trades(portfolio_id, limit=10)
    win_stats = db.get_trade_win_stats(portfolio_id)

    raw_positions = db.get_open_positions(portfolio_id)
    positions = []
    for p in raw_positions:
        priced = _price_with_pnl(p)
        if priced is None:
            logger.error("[%s] %s 查無現價，本次跳過整個帳戶", key, p["symbol"])
            return
        # 技術指標是輔助資訊,查不到不影響本次執行(跟current_price不同,
        # 現價缺了就整批放棄,技術指標缺了只是讓AI少一點參考依據)。
        tech = price_feed.get_technical_snapshot(p["symbol"])
        if tech:
            priced.update({k: v for k, v in tech.items() if k != "latest"})
        positions.append(priced)

    decision = ai_insight.build_trade_decision(config["angle"], portfolio, positions, recent_trades, recent_reports, win_stats)
    if not decision:
        logger.error("[%s] Gemini決策失敗（額度用盡/網路錯誤/回應格式不對），本次跳過", key)
        return

    trade_date = datetime.now().strftime("%Y-%m-%d")
    is_crypto_futures = portfolio_id == "crypto_futures"
    action_lines = []

    for a in decision["actions"]:
        if a["action"] == "hold":
            reasoning = a["reasoning"] or "(無說明)"
            db.record_hold(portfolio_id, trade_date, reasoning)
            # 不管有沒有綁定特定標的都要顯示理由——原本只在有symbol時才加進
            # action_lines，導致「整體觀望、不特定標的」這種hold的理由被
            # 寫進db卻不會出現在Discord訊息裡，使用者只看到「0個動作」卻
            # 不知道AI為什麼不動作(2026-07-30發現)。
            if a["symbol"]:
                action_lines.append(f"• 持有 {a['symbol']}：{reasoning}")
            else:
                action_lines.append(f"• 觀望：{reasoning}")
            continue

        if a["action"] == "close":
            match = next((p for p in positions if p["position_id"] == a["position_id"]), None)
            if match is None:
                logger.warning("[%s] AI指定平倉的position_id不存在，忽略此動作", key)
                continue
            try:
                pnl = db.close_position(match["position_id"], match["current_price"], trade_date, a["reasoning"])
            except ValueError as e:
                # 防止AI決策JSON意外重複同一筆close動作(見db.close_position()
                # 的status防呆)導致整個函式崩潰、當次完全不推播——單一動作
                # 失敗只跳過該動作,不影響同一批次其餘動作跟最終推播。
                logger.warning("[%s] 平倉失敗，忽略此動作：%s", key, e)
                continue
            action_lines.append(
                f"• 平倉 {match['symbol']}（現價{match['current_price']:g}）"
                f" 已實現損益{pnl:+,.2f}：{a['reasoning']}"
            )
            continue

        if a["action"] == "open":
            if not a["symbol"] or a["cash_ratio"] <= 0:
                continue
            side = a["side"] if is_crypto_futures and a["side"] in ("long", "short") else "long"
            leverage = a["leverage"] if is_crypto_futures and a["leverage"] > 1 else 1.0
            price = price_feed.get_price(a["symbol"])
            if price is None or price <= 0:
                logger.warning("[%s] %s 查無現價，忽略此開倉動作", key, a["symbol"])
                continue

            fresh_cash = db.get_portfolio(portfolio_id)["current_cash"]
            cash_ratio = min(a["cash_ratio"], 1.0)
            margin_used = fresh_cash * cash_ratio
            if margin_used <= 0:
                continue
            quantity = margin_used * leverage / price
            db.open_position(portfolio_id, a["symbol"], side, quantity, price, leverage, trade_date, a["reasoning"])
            action_lines.append(
                f"• 開倉 {a['symbol']} {side} 數量{quantity:g}（價{price:g}，"
                f"槓桿{leverage:g}x，動用現金{margin_used:,.2f}）：{a['reasoning']}"
            )

    # 事件觸發機制(2026-07-31,使用者APPROVED,07-31再次確認擴及discretionary)：
    # crypto_futures_portfolio/crypto_discretionary_portfolio都改成
    # event-triggered排程(見check_triggers.py)，只有tw_stock_portfolio維持
    # 原本排程不變，故next_trigger只在這兩個帳戶持久化。
    if key in ("crypto_futures_portfolio", "crypto_discretionary_portfolio"):
        next_trigger = decision.get("next_trigger") or {}
        existing = db.get_portfolio_trigger(portfolio_id)
        min_hours = existing["min_hours_between_calls"] if existing else 168.0
        latest_trades = db.get_recent_trades(portfolio_id, limit=1)
        latest_log_id = latest_trades[0]["log_id"] if latest_trades else None
        db.set_portfolio_trigger(
            portfolio_id,
            json.dumps(next_trigger.get("price_triggers", [])),
            json.dumps(next_trigger.get("news_keywords", [])),
            min_hours,
            latest_log_id,
        )
        logger.info("[%s] 已更新下次觸發條件：%s", key, next_trigger.get("reasoning", "(無說明)"))

    portfolio = db.get_portfolio(portfolio_id)
    final_positions = []
    for p in db.get_open_positions(portfolio_id):
        priced = _price_with_pnl(p)
        # 交易已經執行完了,查價失敗只影響報告顯示,不能因此不推播——
        # 用avg_cost頂替current_price純粹是顯示用途(market_value退回margin_used,
        # 不假裝算得出unrealized_pnl),不影響db裡任何已寫入的數字。
        final_positions.append(priced if priced is not None else {
            **p, "current_price": "?", "unrealized_pnl": 0,
            "market_value": p["avg_cost"] * p["quantity"] / p["leverage"],
        })

    embed = digest_format.build_portfolio_embed(config["channel_title"], trade_date, portfolio, final_positions, action_lines)
    ok, status, err = send_webhook(webhook_url, embed)
    if ok:
        logger.info("[%s] 推播成功，%d 個動作、%d 筆持倉", key, len(action_lines), len(final_positions))
    else:
        logger.error("[%s] 推播失敗：HTTP %s %s", key, status, err)


def main():
    parser = argparse.ArgumentParser(description="本地爬蟲 → Discord Webhook 推播")
    parser.add_argument("--source", choices=list(SOURCE_REGISTRY.keys()) + ["all", "daily_recap"]
                                     + list(DIGEST_CHANNELS.keys()) + list(META_SUMMARY_CHANNELS.keys())
                                     + list(PORTFOLIO_CHANNELS.keys()),
                        help="執行單一來源（與 --scholarship 二選一）")
    parser.add_argument("--scholarship", action="store_true",
                        help="批次執行所有獎學金來源")
    parser.add_argument("--internship", action="store_true",
                        help="批次執行所有台灣實習來源")
    # 2026-07-31新增(排程精簡)：這三個時間點原本各自拆成多個獨立Windows
    # Scheduled Task(7個20:00+4個09:00+2個20:30=13個)，但同一時間點的
    # 來源沒有理由分開排程，改成各一個flag內部迴圈跑完，Task Scheduler
    # 裡的任務數從22個減到約11個，見docs/scheduled_task_consolidation.md。
    parser.add_argument("--digest-all", action="store_true",
                        help="批次執行所有晚間彙整頻道(20:00)")
    parser.add_argument("--meta-all", action="store_true",
                        help="批次執行所有大總結頻道(20:30)")
    parser.add_argument("--daily-official", action="store_true",
                        help="批次執行所有平日官方資料來源(fed/etf0050/macro_fred/twse_tsmc/twse_chunghwa,09:00)")
    args = parser.parse_args()

    flags = (args.source, args.scholarship, args.internship, args.digest_all, args.meta_all, args.daily_official)
    if not any(flags):
        parser.print_help()
        sys.exit(1)
    if sum(bool(x) for x in flags) > 1:
        parser.error("--source / --scholarship / --internship / --digest-all / --meta-all / --daily-official 六者互斥，一次只能選一個")

    db.init_db()

    if args.scholarship:
        run_scholarship()
    elif args.internship:
        run_internship()
    elif args.digest_all:
        # 批次迴圈本身也接一層例外(即使run_digest_channel理論上該自己接
        # 完)，避免任何未預期例外讓後面的頻道整批不執行——這條保證在改成
        # 批次flag之前是Windows Task Scheduler天然提供的(每個頻道獨立
        # process)，合併執行後要自己補上。
        for key in DIGEST_CHANNELS:
            try:
                run_digest_channel(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此頻道", exc_info=True)
    elif args.meta_all:
        for key in META_SUMMARY_CHANNELS:
            try:
                run_meta_summary_channel(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此頻道", exc_info=True)
    elif args.daily_official:
        # macro_fred/etf0050原本只排平日(非交易日執行只會抓到空資料靜默
        # 省略，見各自docstring既有容錯設計)，twse_tsmc/twse_chunghwa/fed
        # 本來就是Daily，統一每天執行不影響功能，順便省掉平日/每日兩種
        # 排程頻率的差異。
        for key in ("fed", "etf0050", "macro_fred", "twse_tsmc", "twse_chunghwa"):
            # run_source()本身已有完整try/except(既有設計)，這裡再包一層
            # 純粹是跟--digest-all/--meta-all維持同一種批次防護風格一致。
            try:
                run_source(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此來源", exc_info=True)
    elif args.source in DIGEST_CHANNELS:
        run_digest_channel(args.source)
    elif args.source in META_SUMMARY_CHANNELS:
        run_meta_summary_channel(args.source)
    elif args.source in PORTFOLIO_CHANNELS:
        run_portfolio_channel(args.source)
    elif args.source == "daily_recap":
        run_daily_recap()
    elif args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
