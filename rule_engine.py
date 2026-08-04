"""
rule_engine.py — 規則式模擬持倉決策引擎(2026-08-04新增，使用者確認取代
ai_insight.build_trade_decision()的AI判斷)。

跟ai_insight.build_trade_decision()完全相同的輸入/輸出介面(main.py的
DECISION_ENGINE切換點靠這個相容性做drop-in替換)，用SMA5/SMA20黃金/死亡
交叉規則取代Gemini呼叫，零AI依賴、零額度成本。策略參數(進場訊號/停利%/
部位比例/槓桿)是使用者逐項確認過的，不是隨意假設。

策略摘要：
- 進場：SMA5 > SMA20(黃金交叉，用「目前sma5是否大於sma20」近似判斷，不
  追溯前一筆再比對relation，這是選定的最小可行判斷法)且該標的目前無
  持倉 → 開多倉
- 出場(任一成立)：
  ①未實現損益達保證金(margin_used)的+10%(停利)
  ②SMA5 < SMA20(死亡交叉，趨勢反轉出場)
- 明確沒有「規則式」停損——使用者已看過「無停損保護、單向下跌會持有到底」
  的風險提示後確認要這樣做，這裡不擅自加自動停損規則。但補了一個窄範圍
  的AI安全閥(2026-08-04新增)：單一部位虧損達保證金30%、或整個帳戶總價值
  較起始本金回撤30%，任一成立就臨時問AI「這筆要不要切損」(只問這一筆，
  不做完整交易決策)，避免完全沒有任何止血機制。同一部位4小時內不重複
  詢問(沿用check_triggers.py既有的db.get_source節流模式)，避免虧損長期
  持平時每20分鐘重複打AI額度。
- 部位大小：cash_ratio固定0.2(動用現金兩成)
- 槓桿：crypto_futures_portfolio固定20倍(使用者確認10-100倍皆可接受，
  這裡選中段值)；其餘帳戶固定1.0(main.py::run_portfolio_channel本來就會
  強制非futures帳戶回到1.0，這裡明確寫出來只是對齊，不依賴那層防呆)
- 死亡交叉只平倉、不自動反手做空——範圍保守，之後要加是另一個決策

WATCHLISTS是這支模組唯一的「標的清單」常數，之後要調整觀察哪些標的直接
改這裡，不用動決策邏輯本身。crypto兩帳戶(BTC/ETH)是使用者明講的；tw_stock
沒有明講，暫定沿用既有etf0050/twse_tsmc兩個digest來源對應的0050/2330，
使用者已在計畫審核階段看過這個假設。
"""
from datetime import datetime, timezone

import ai_insight
import db
import price_feed

WATCHLISTS = {
    "crypto_futures": ["BTC", "ETH"],
    "crypto_discretionary": ["BTC", "ETH"],
    "tw_stock": ["0050", "2330"],
}

ENTRY_CASH_RATIO = 0.2
FUTURES_LEVERAGE = 20.0
TAKE_PROFIT_PCT = 0.10

# AI安全閥(2026-08-04新增，使用者確認)：規則本身沒有停損，虧損/回撤大到
# 這個門檻才臨時問AI要不要切損。門檻跟節流冷卻時間都是使用者確認的數字。
STOP_LOSS_ESCALATION_PCT = 0.30
STOP_LOSS_COOLDOWN_HOURS = 4.0


def _margin_used(position: dict) -> float:
    return position["avg_cost"] * position["quantity"] / position["leverage"]


def _stop_loss_source_id(position_id: str) -> str:
    return f"stop_loss_escalation.{position_id}"


def _in_cooldown(position_id: str) -> bool:
    """4小時內問過同一個position就跳過，不重複打AI額度(沿用
    check_triggers.py的FIRST_RUN_RETRY_BACKOFF_HOURS同一套source表模式)。"""
    record = db.get_source(_stop_loss_source_id(position_id))
    if not record or not record.get("last_fetched_at"):
        return False
    last_attempt = datetime.fromisoformat(record["last_fetched_at"])
    if last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    hours_since = (datetime.now(timezone.utc) - last_attempt).total_seconds() / 3600
    return hours_since < STOP_LOSS_COOLDOWN_HOURS


def _mark_stop_loss_attempt(position_id: str, symbol: str):
    source_id = _stop_loss_source_id(position_id)
    db.upsert_source(source_id, f"{symbol}停損評估節流", "stop_loss_escalation", "")
    db.record_fetch_success(source_id)


def _check_stop_loss_escalation(symbol: str, position: dict, portfolio: dict,
                                 loss_pct: float, account_drawdown_pct: float) -> dict | None:
    """單部位虧損或帳戶回撤達門檻時，問AI「這筆要不要切損」。回傳
    close action dict(AI說要切)或None(AI說不切/呼叫失敗/還在冷卻中，
    這三種情況呼叫端都應該維持既有的hold邏輯，不能混為一談去猜AI的
    意思)。"""
    if loss_pct < STOP_LOSS_ESCALATION_PCT and account_drawdown_pct < STOP_LOSS_ESCALATION_PCT:
        return None

    position_id = position["position_id"]
    if _in_cooldown(position_id):
        return None

    if loss_pct >= STOP_LOSS_ESCALATION_PCT:
        trigger_reason = f"單部位虧損達保證金的{loss_pct:.0%}(門檻{STOP_LOSS_ESCALATION_PCT:.0%})"
    else:
        trigger_reason = f"帳戶總值較起始本金回撤{account_drawdown_pct:.0%}(門檻{STOP_LOSS_ESCALATION_PCT:.0%})"

    # 不論AI呼叫成功與否都要記錄嘗試時間，避免AI暫時失敗時每20分鐘重打。
    _mark_stop_loss_attempt(position_id, symbol)
    result = ai_insight.assess_stop_loss(position, portfolio, trigger_reason)
    if result is None or not result.get("cut"):
        return None

    return {
        "action": "close", "symbol": symbol, "side": position["side"],
        "cash_ratio": 0.0, "leverage": position["leverage"],
        "position_id": position_id,
        "reasoning": f"AI安全閥觸發切損({trigger_reason})：{result.get('reasoning', '(無說明)')}",
    }


def _decide_existing_position(symbol: str, position: dict, portfolio: dict,
                               account_drawdown_pct: float) -> tuple[dict, dict | None]:
    """已有持倉的標的：回傳(action_dict, price_trigger_dict_or_None)。"""
    sma5 = position.get("sma5")
    sma20 = position.get("sma20")
    unrealized_pnl = position.get("unrealized_pnl")
    margin_used = _margin_used(position)

    if unrealized_pnl is not None and margin_used > 0 and unrealized_pnl >= margin_used * TAKE_PROFIT_PCT:
        action = {
            "action": "close", "symbol": symbol, "side": position["side"],
            "cash_ratio": 0.0, "leverage": position["leverage"],
            "position_id": position["position_id"],
            "reasoning": (
                f"未實現損益{unrealized_pnl:+.2f}已達保證金{margin_used:.2f}"
                f"的+{TAKE_PROFIT_PCT:.0%}，停利出場"
            ),
        }
        return action, None

    if sma5 is not None and sma20 is not None and sma5 < sma20:
        action = {
            "action": "close", "symbol": symbol, "side": position["side"],
            "cash_ratio": 0.0, "leverage": position["leverage"],
            "position_id": position["position_id"],
            "reasoning": f"SMA5({sma5:.4g}) < SMA20({sma20:.4g})，死亡交叉，趨勢反轉出場",
        }
        return action, None

    # AI安全閥(2026-08-04)：規則本身沒有停損，虧損/回撤達門檻時臨時問AI
    # 要不要切損——只在這裡插入，不影響上面兩個既有規則判斷的優先順序
    # (停利/死亡交叉本來就是規則能處理的正常出場，不需要問AI)。
    loss_pct = (-unrealized_pnl / margin_used) if (unrealized_pnl is not None and unrealized_pnl < 0 and margin_used > 0) else 0.0
    escalated_close = _check_stop_loss_escalation(symbol, position, portfolio, loss_pct, account_drawdown_pct)
    if escalated_close is not None:
        return escalated_close, None

    if sma5 is not None and sma20 is not None:
        hold_reason = f"SMA5({sma5:.4g})/SMA20({sma20:.4g})、未實現損益{unrealized_pnl:+.2f}，尚未達出場條件"
    else:
        hold_reason = "持有中(技術指標資料不足)，尚未達停利條件"
    if loss_pct >= STOP_LOSS_ESCALATION_PCT or account_drawdown_pct >= STOP_LOSS_ESCALATION_PCT:
        hold_reason += "（已達AI安全閥門檻，已詢問或仍在節流冷卻中，AI判斷維持持有/暫無回應）"
    action = {
        "action": "hold", "symbol": symbol, "side": position["side"],
        "cash_ratio": 0.0, "leverage": position["leverage"], "position_id": "",
        "reasoning": hold_reason,
    }
    # 已持倉：死亡交叉是下一個在意的事件，門檻設在sma20，價格跌破時代表
    # 接近/形成死亡交叉，讓check_triggers.py既有機制自然再評估一次。
    trigger = {"symbol": symbol, "below": sma20} if sma20 is not None else None
    return action, trigger


def _decide_new_entry(symbol: str, is_futures: bool) -> tuple[dict | None, dict | None]:
    """無持倉的標的：回傳(action_dict_or_None, price_trigger_dict_or_None)。
    技術指標資料不足(剛上市/歷史不夠)時兩者皆回傳None，這次不對這個標的
    做任何判斷。"""
    snapshot = price_feed.get_technical_snapshot(symbol)
    if not snapshot or "sma5" not in snapshot or "sma20" not in snapshot:
        return None, None

    sma5, sma20 = snapshot["sma5"], snapshot["sma20"]
    if sma5 > sma20:
        action = {
            "action": "open", "symbol": symbol, "side": "long",
            "cash_ratio": ENTRY_CASH_RATIO,
            "leverage": FUTURES_LEVERAGE if is_futures else 1.0,
            "position_id": "",
            "reasoning": f"SMA5({sma5:.4g}) > SMA20({sma20:.4g})，黃金交叉，進場",
        }
        # 開倉後不用在這裡設trigger——下一輪重新評估時這個symbol會有
        # position了，走_decide_existing_position自己設下一個trigger。
        return action, None

    action = {
        "action": "hold", "symbol": symbol, "side": "long",
        "cash_ratio": 0.0, "leverage": 1.0, "position_id": "",
        "reasoning": f"SMA5({sma5:.4g}) <= SMA20({sma20:.4g})，尚未黃金交叉，觀望",
    }
    # 尚未持倉：黃金交叉是下一個在意的事件，門檻設在sma20，價格漲破時
    # 代表接近/形成黃金交叉。
    trigger = {"symbol": symbol, "above": sma20}
    return action, trigger


def build_trade_decision(angle, portfolio, positions, recent_trades, recent_reports, win_stats=None) -> dict | None:
    """跟ai_insight.build_trade_decision()相同介面(main.py靠這個相容性做
    drop-in替換)。angle/recent_trades/recent_reports/win_stats這次規則
    不使用，只是保留參數相容性，呼叫端完全不用因為切換引擎而改寫呼叫邏輯。

    回傳{"actions": [...], "next_trigger": {...}}——即使完全沒有動作也回傳
    空actions清單，不回傳None(main.py把None視為「決策失敗，整批跳過」，
    跟「規則判斷完是no-op」是不同語意，不能混用)。"""
    portfolio_id = portfolio.get("portfolio_id")
    watchlist = WATCHLISTS.get(portfolio_id, [])
    is_futures = portfolio_id == "crypto_futures"
    open_by_symbol = {p["symbol"]: p for p in positions}

    # 帳戶總值回撤(2026-08-04新增，AI安全閥用)：現金+全部持倉市值，跟起始
    # 本金比較。一次算好給每個部位共用，不必每個部位各自重算。
    starting_capital = portfolio.get("starting_capital") or 0
    total_value = portfolio.get("current_cash", 0) + sum(p.get("market_value", 0) or 0 for p in positions)
    account_drawdown_pct = max(0.0, (starting_capital - total_value) / starting_capital) if starting_capital > 0 else 0.0

    # 涵蓋清單=觀察清單 ∪ 目前實際持倉的標的(以防有不在觀察清單裡的既有
    # 持倉，仍然要能被規則判斷停利/死亡交叉出場，不會被規則引擎晾在一邊)。
    symbols = list(dict.fromkeys(watchlist + list(open_by_symbol.keys())))

    actions = []
    price_triggers = []

    for symbol in symbols:
        position = open_by_symbol.get(symbol)
        if position is not None:
            action, trigger = _decide_existing_position(symbol, position, portfolio, account_drawdown_pct)
        elif symbol in watchlist:
            action, trigger = _decide_new_entry(symbol, is_futures)
        else:
            continue

        if action is not None:
            actions.append(action)
        if trigger is not None:
            price_triggers.append(trigger)

    return {
        "actions": actions,
        "next_trigger": {
            "price_triggers": price_triggers,
            "news_keywords": [],
            "reasoning": f"規則引擎(SMA5/20交叉)，觀察{len(symbols)}檔：{', '.join(symbols) or '(無)'}",
        },
    }
