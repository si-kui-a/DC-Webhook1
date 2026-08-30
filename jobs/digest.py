"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import ai_insight
import db
import index_dca_engine
import price_feed
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050, macro_fred, twse_financials, rental_search
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import substack_generic
from scrapers import semi_supply_chain
import digest_format
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util
from scrapers import internship_mol
from scrapers import internship_104
from scrapers import internship_rich
from scrapers import internship_yes123
from scrapers import internship_gift
from scrapers import internship_util
from scrapers import sig_content_watch
from jobs.engine import compute_summary
from jobs.paths import TAIWAN_TZ

logger = logging.getLogger("main")

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
    if key == "rental_search" and not webhook_url:
        webhook_url = os.getenv("WEBHOOK_HOUSE_591")
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
