"""
check_triggers.py — 加密貨幣模擬持倉「事件觸發」規則式檢查(2026-07-31,使用者APPROVED)。

取代main.py --source crypto_futures_portfolio/crypto_discretionary_portfolio
每小時無條件呼叫AI的排程方式:這支改成高頻排程(建議15-30分鐘一次,見
.github/workflows/scheduler.yml)，純規則
檢查以下維度，任一觸發才呼叫main.py的run_portfolio_channel()——AI只在真的
有意義的變化時才被呼叫，省下大部分免費層每日20次配額(這兩個帳戶原本各自
每小時=24次/天，加起來48次/天，單獨就超過整包配額兩倍多，這是2026-07-31
發現的實測問題，不是理論推測)。

四個觸發維度(使用者確認2026-07-30/07-31)：
- 數值面：price_triggers(存在portfolio_trigger,AI每次決策後自己設定下次
  的價格門檻)
- 消息面：news_keywords(同上,AI自訂關鍵字,比對crypto_digest來源在上次
  評估後新抓到的項目)
- 市場面：重用scrapers/macro_fred.py既有的規則式警報(any_alert_triggered())，
  全域判斷,不逐投組儲存門檻，兩個帳戶共用同一個市場面判斷結果
- 保底機制：min_hours_between_calls(預設168小時=1週,使用者確認拉長至此，
  避免盤整期太頻繁重新評估)，不管有沒有觸發，超過這個時數還是強制重新
  評估一次，避免長期不觸發就永遠不更新策略

crypto_futures(合約,可槓桿可放空)跟crypto_discretionary(現貨,長期持有,
不可槓桿)風險特性不同但都讀同一份市場面/消息面資料來源(digest_report.
crypto_meta + crypto_digest新聞來源)，結構上同樣適用事件觸發，
discretionary風險更低甚至更站得住腳拉開評估頻率(2026-07-31使用者確認
比照套用)。兩個帳戶各自獨立一筆portfolio_trigger,互不影響。

第一次執行(某帳戶的portfolio_trigger還沒有任何列)一律視為觸發——沒有
門檻可比對，交由AI先做一次初始決策並設定好之後的門檻。

純規則、零AI，只有真的觸發、呼叫main.run_portfolio_channel()時才會用到AI。
"""
import json
import logging
from datetime import datetime, timezone

import db
import main
import price_feed
from scrapers import macro_fred, substack_generic

logger = logging.getLogger("check_triggers")

PORTFOLIO_KEYS = ["crypto_futures_portfolio", "crypto_discretionary_portfolio"]

# 「首次執行」重試退避(2026-07-31使用者確認，修正實測踩到的bug)：AI失敗
# 時portfolio_trigger從頭到尾不會被寫入(只有成功才會set_portfolio_trigger)，
# 若不額外記錄「上次嘗試時間」，每20分鐘的check_triggers排程會一直判定成
# 「尚無觸發條件紀錄=首次執行」不斷重試——2026-07-31實測踩過：早上10:33到
# 下午15:12連續14次重試，額度用盡還拖累tw_stock_portfolio當天的Gemini呼叫
# 失敗。這裡刻意不重用portfolio_trigger表本身記錄嘗試時間(那張表的
# min_hours_between_calls會被run_portfolio_channel()的「成功後沿用舊值」
# 邏輯繼續帶下去，寫一筆2小時的暫時值會永久污染掉之後應有的168小時週期)，
# 改用source表的last_fetched_at欄位(db.get_source/record_fetch_success)
# 單純標記「上次嘗試時間」，跟portfolio_trigger的語意完全分開。
FIRST_RUN_RETRY_BACKOFF_HOURS = 2.0


def _retry_source_id(portfolio_id: str) -> str:
    return f"portfolio_retry.{portfolio_id}"
# 消息面比對用的新聞來源，跟main.py的crypto_digest頻道共用同一批
# substack來源(見main.py DIGEST_CHANNELS["crypto_digest"]["source_ids"])，
# 兩個帳戶共用同一批新聞來源，各自的news_keywords不同。
NEWS_SOURCE_IDS = [s[0] for s in substack_generic.CRYPTO_FEEDS]


def _price_trigger_hit(price_triggers: list[dict]) -> str | None:
    for t in price_triggers:
        symbol = t.get("symbol")
        if not symbol:
            continue
        price = price_feed.get_price(symbol)
        if price is None:
            continue
        above = t.get("above")
        below = t.get("below")
        if above is not None and price >= above:
            return f"{symbol}價格{price:g}已突破上緣{above:g}"
        if below is not None and price <= below:
            return f"{symbol}價格{price:g}已跌破下緣{below:g}"
    return None


def _news_trigger_hit(news_keywords: list[str], since_iso: str) -> str | None:
    if not news_keywords:
        return None
    items = db.get_items_since(NEWS_SOURCE_IDS, since_iso)
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        text_lower = text.lower()
        for kw in news_keywords:
            if kw and kw.lower() in text_lower:
                return f"新聞命中關鍵字「{kw}」：{item.get('title', '')[:40]}"
    return None


def _check_one(portfolio_key: str, market_alert: bool) -> bool:
    """檢查單一帳戶,回傳True代表本次有觸發並呼叫AI決策。market_alert由
    呼叫端算一次共用(市場面是全域判斷,不需要每個帳戶重複打FRED API)。"""
    portfolio_id = main.PORTFOLIO_CHANNELS[portfolio_key]["portfolio_id"]
    trigger = db.get_portfolio_trigger(portfolio_id)

    if trigger is None:
        retry_source_id = _retry_source_id(portfolio_id)
        last_attempt = db.get_source(retry_source_id)
        if last_attempt and last_attempt.get("last_fetched_at"):
            attempted_at = datetime.fromisoformat(last_attempt["last_fetched_at"])
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=timezone.utc)
            hours_since_attempt = (datetime.now(timezone.utc) - attempted_at).total_seconds() / 3600
            if hours_since_attempt < FIRST_RUN_RETRY_BACKOFF_HOURS:
                logger.info(
                    "[%s] 尚無觸發條件紀錄，但距上次嘗試僅%.1f小時(退避%.0f小時)，暫緩重試",
                    portfolio_id, hours_since_attempt, FIRST_RUN_RETRY_BACKOFF_HOURS,
                )
                return False

        logger.info("[%s] 尚無觸發條件紀錄(首次執行)，直接呼叫AI決策一次", portfolio_id)
        # 呼叫AI前先標記嘗試時間——AI若失敗，portfolio_trigger不會被寫入，
        # 但這筆嘗試時間會擋下接下來FIRST_RUN_RETRY_BACKOFF_HOURS小時內的
        # 重試，不會每20分鐘就再打一次(見模組頂部設計說明)。
        db.upsert_source(retry_source_id, f"{portfolio_id}首次執行重試標記", "portfolio_retry", "")
        db.record_fetch_success(retry_source_id)
        main.run_portfolio_channel(portfolio_key)
        return True

    reasons = []

    price_triggers = json.loads(trigger["price_triggers"] or "[]")
    hit = _price_trigger_hit(price_triggers)
    if hit:
        reasons.append(f"數值面：{hit}")

    news_keywords = json.loads(trigger["news_keywords"] or "[]")
    hit = _news_trigger_hit(news_keywords, trigger["set_at"])
    if hit:
        reasons.append(f"消息面：{hit}")

    if market_alert:
        reasons.append("市場面：總經指標警報觸發(VIX/殖利率/信用利差/那斯達克/費半任一達閾值)")

    set_at = datetime.fromisoformat(trigger["set_at"])
    if set_at.tzinfo is None:
        set_at = set_at.replace(tzinfo=timezone.utc)
    hours_elapsed = (datetime.now(timezone.utc) - set_at).total_seconds() / 3600
    if hours_elapsed >= trigger["min_hours_between_calls"]:
        reasons.append(f"保底機制：距上次評估已{hours_elapsed:.1f}小時，強制重新評估")

    if not reasons:
        logger.info("[%s] 無觸發條件成立，略過本次AI決策", portfolio_id)
        return False

    logger.info("[%s] 觸發原因：%s", portfolio_id, "；".join(reasons))
    main.run_portfolio_channel(portfolio_key)
    return True


def check_and_maybe_run() -> dict[str, bool]:
    """對兩個加密貨幣帳戶各自檢查一次,回傳{portfolio_key: 是否觸發}。
    市場面只算一次(全域共用,不重複打FRED API)。"""
    market_alert = macro_fred.any_alert_triggered()
    return {key: _check_one(key, market_alert) for key in PORTFOLIO_KEYS}


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check_and_maybe_run()
