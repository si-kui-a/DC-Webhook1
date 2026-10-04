"""
db.py — SQLite 存取層。
不用 ORM：資料量小（週報/月報頻率），直接 sqlite3 標準庫即可，減少依賴。
"""
import re
import sqlite3
import hashlib
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """首次執行時建表。冪等：重複執行不會清空既有資料。"""
    conn = get_conn()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def upsert_source(source_id: str, name: str, category: str, base_url: str):
    conn = get_conn()
    conn.execute(
        """INSERT INTO source (source_id, name, category, base_url)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET name=excluded.name, base_url=excluded.base_url""",
        (source_id, name, category, base_url),
    )
    conn.commit()
    conn.close()


def _normalize_for_dedup(text: str) -> str:
    """PAT-27：docstring原本就宣稱「避免空白/全半形差異造成重複推播」，
    但實作只有.strip()(掐頭去尾)，沒有真的處理內部連續空白或全半形——
    PAT-06已經記過cbc同一篇文章的RSS標題會有全半形標點差異，這個落差
    會讓同一篇文章因為標點寬度不同被當成兩篇不同項目重複推播。這裡才是
    docstring原本承諾的正規化：NFKC轉換(全形英數字/標點→半形)+內部
    連續空白壓成單一空白，跟一旁strip()疊加使用。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def make_dedup_key(source_id: str, title: str, url: str) -> str:
    """去重鍵：來源 + 標題 + URL 正規化後雜湊。標題正規化可避免空白/全半形差異造成重複推播。"""
    normalized = f"{source_id}|{_normalize_for_dedup(title)}|{_normalize_for_dedup(url)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def insert_item_if_new(source_id: str, title: str, summary: str, url: str, published_at: str | None) -> dict | None:
    """
    寫入新項目；若 dedup_key 已存在則回傳 None（代表已抓過，不需推播）。
    回傳 dict 供推播層直接使用，避免多一次查詢。
    """
    dedup_key = make_dedup_key(source_id, title, url)
    conn = get_conn()
    existing = conn.execute("SELECT item_id FROM item WHERE dedup_key = ?", (dedup_key,)).fetchone()
    if existing:
        conn.close()
        return None

    item_id = str(uuid.uuid4())
    fetched_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO item (item_id, source_id, title, summary, url, published_at, fetched_at, dedup_key, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
        (item_id, source_id, title, summary, url, published_at, fetched_at, dedup_key),
    )
    conn.commit()
    conn.close()
    return {
        "item_id": item_id,
        "source_id": source_id,
        "title": title,
        "summary": summary,
        "url": url,
        "published_at": published_at,
        "fetched_at": fetched_at,
    }


def mark_published(item_id: str):
    conn = get_conn()
    conn.execute("UPDATE item SET status='published' WHERE item_id=?", (item_id,))
    conn.commit()
    conn.close()


def mark_seeded_historical(item_id: str):
    """首次執行安全閘門存檔、但從未實際推播過的項目用這個，不要用 mark_published
    （沒有真的送出過）也不要留在預設的 'new'（'new' 保留給真正待推播的項目，
    避免將來的 catch-up/replay 邏輯誤把這批舊資料當成待推播）。"""
    conn = get_conn()
    conn.execute("UPDATE item SET status='seeded_historical' WHERE item_id=?", (item_id,))
    conn.commit()
    conn.close()


def mark_stale_not_today(item_id: str):
    """晚間彙整頻道要求「只收錄台灣時區當日發佈的文章」（見main.py
    _is_today_in_taiwan），published_at不是今天的項目用這個標記——跟
    seeded_historical（首次執行閘門）、digest_omitted（篇幅裝不下）語意
    都不同，是「這篇本身不屬於今天」，維持狀態欄位語意誠實。"""
    conn = get_conn()
    conn.execute("UPDATE item SET status='stale_not_today' WHERE item_id=?", (item_id,))
    conn.commit()
    conn.close()


def mark_digest_omitted(item_id: str):
    """晚間彙整頻道因篇幅（最多2則訊息裝不下）被省略的項目用這個，不是
    'published'（沒有真的顯示給使用者看過內容）也不是'seeded_historical'
    （那是首次執行安全閘門的語意，跟這裡「篇幅不夠」的原因不同），維持
    狀態欄位語意的誠實，供之後回顧「哪些內容常態性被省略」使用。"""
    conn = get_conn()
    conn.execute("UPDATE item SET status='digest_omitted' WHERE item_id=?", (item_id,))
    conn.commit()
    conn.close()


def discard_unsent_items(item_ids: list[str]) -> int:
    """彙整頻道在AI彙整或推播完全失敗時用：刪掉這次剛寫入、仍是'new'的
    項目，讓下一次執行重新抓到時還算「新」，可以重試。不刪的話
    insert_item_if_new()會把它們當成已處理，那天的彙整就永久漏掉
    (2026-07-31~09-24本機error.log共37次)。只刪status='new'，已推播
    或已標其他狀態的不受影響。"""
    if not item_ids:
        return 0
    conn = get_conn()
    placeholders = ",".join("?" * len(item_ids))
    cur = conn.execute(
        f"DELETE FROM item WHERE status='new' AND item_id IN ({placeholders})", item_ids
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def update_summary(item_id: str, summary: str):
    """摘要是在 insert_item_if_new() 之後才算出來的（cbc 直接用 RSS
    description，fed/tsmc 需要多發一次 detail 頁請求），先用 summary=None
    insert，算出來後再補寫回去，避免對「首次執行安全閘門」擋下、根本不會
    被推播的項目也白白花算力做摘要。"""
    conn = get_conn()
    conn.execute("UPDATE item SET summary=? WHERE item_id=?", (summary, item_id))
    conn.commit()
    conn.close()


def get_summary_for_date(source_id: str, published_at: str) -> str | None:
    """依source_id+published_at取回該筆的summary全文,供大總結頻道讀取
    「當天某頻道已經產出的完整報告內容」(不重新抓取原始資料，直接沿用
    既有的item.summary，見jobs/daily_recap.py)。多筆符合時
    取最新寫入的一筆。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT summary FROM item WHERE source_id=? AND published_at=? "
        "ORDER BY fetched_at DESC LIMIT 1",
        (source_id, published_at),
    ).fetchone()
    conn.close()
    return row["summary"] if row else None


def log_delivery(item_id: str | None, channel: str, http_status: int | None, error_message: str | None = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO delivery_log (item_id, channel, sent_at, http_status, error_message) VALUES (?, ?, ?, ?, ?)",
        (item_id, channel, datetime.now(timezone.utc).isoformat(), http_status, error_message),
    )
    conn.commit()
    conn.close()


def record_fetch_success(source_id: str):
    conn = get_conn()
    conn.execute(
        "UPDATE source SET last_fetched_at=?, fail_count=0 WHERE source_id=?",
        (datetime.now(timezone.utc).isoformat(), source_id),
    )
    conn.commit()
    conn.close()


def count_items_for_source(source_id: str) -> int:
    """回傳這個來源目前在 db 裡已有的項目數。0 代表這是它第一次執行過 fetch+dedup。"""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS n FROM item WHERE source_id = ?", (source_id,)).fetchone()
    conn.close()
    return row["n"] if row else 0


def record_fetch_failure(source_id: str) -> int:
    """回傳更新後的 fail_count，供呼叫端判斷是否要觸發告警。"""
    conn = get_conn()
    conn.execute("UPDATE source SET fail_count = fail_count + 1 WHERE source_id=?", (source_id,))
    conn.commit()
    row = conn.execute("SELECT fail_count FROM source WHERE source_id=?", (source_id,)).fetchone()
    conn.close()
    return row["fail_count"] if row else 0


# ── 模擬持倉(紙上帳戶) ──
# PnL公式(已核對):
#   margin_used = avg_cost * quantity / leverage  (開倉時從current_cash扣除的金額)
#   long  unrealized_pnl = (price - avg_cost) * quantity
#   short unrealized_pnl = (avg_cost - price) * quantity
#   平倉時歸還 current_cash += margin_used + pnl
# leverage=1時margin_used等於全額本金,跟現貨/台股語意一致。

PORTFOLIO_SEEDS = [
    ("tw_stock", "台股模擬帳戶", "TWD", 1000.0),
    ("crypto_futures", "幣圈合約模擬帳戶", "USDT", 100.0),
    ("crypto_discretionary", "幣圈自主判斷模擬帳戶", "USDT", 100.0),
]


def init_portfolios():
    """冪等：3個模擬帳戶第一次執行時建立初始本金,已存在則不覆寫
    current_cash(避免每次啟動把已經在跑的模擬倉位本金重置回起始值)。"""
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    for portfolio_id, name, currency, starting_capital in PORTFOLIO_SEEDS:
        conn.execute(
            """INSERT INTO portfolio (portfolio_id, name, currency, starting_capital, current_cash, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(portfolio_id) DO NOTHING""",
            (portfolio_id, name, currency, starting_capital, starting_capital, now),
        )
    conn.commit()
    conn.close()


def get_portfolio(portfolio_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM portfolio WHERE portfolio_id=?", (portfolio_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_positions(portfolio_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM position WHERE portfolio_id=? AND status='open' ORDER BY opened_at",
        (portfolio_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def open_position(portfolio_id: str, symbol: str, side: str, quantity: float,
                   avg_cost: float, leverage: float, trade_date: str, reasoning: str) -> str:
    """開倉：從current_cash扣除margin_used,寫入position+trade_log。"""
    margin_used = avg_cost * quantity / leverage
    position_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        """INSERT INTO position (position_id, portfolio_id, symbol, quantity, avg_cost, leverage, side, opened_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
        (position_id, portfolio_id, symbol, quantity, avg_cost, leverage, side, now),
    )
    conn.execute(
        "UPDATE portfolio SET current_cash = current_cash - ? WHERE portfolio_id=?",
        (margin_used, portfolio_id),
    )
    conn.execute(
        """INSERT INTO trade_log (portfolio_id, trade_date, action, symbol, quantity, price, reasoning, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (portfolio_id, trade_date, f"open_{side}", symbol, quantity, avg_cost, reasoning, now),
    )
    conn.commit()
    conn.close()
    return position_id


def close_position(position_id: str, price: float, trade_date: str, reasoning: str,
                   costs: float = 0.0, liquidated: bool = False) -> float:
    """平倉：歸還margin_used+pnl給current_cash,回傳這筆realized_pnl。
    costs(手續費+資金費率,見rule_engine.trade_costs)從pnl扣除；liquidated=True
    時pnl固定為−margin_used——強制平倉後保證金全數歸零,不會再倒扣現金
    (2026-10-04前虧損可以超過保證金,現金可以變負數)。"""
    conn = get_conn()
    pos = conn.execute("SELECT * FROM position WHERE position_id=?", (position_id,)).fetchone()
    if pos is None:
        conn.close()
        raise ValueError(f"position_id不存在: {position_id}")
    if pos["status"] != "open":
        # 防止對同一個position_id重複平倉(例如AI決策JSON意外重複同一筆
        # close動作)導致margin_used+pnl被重複加回current_cash、現金餘額
        # 灌水失真——2026-07-30自我檢討時發現的真實風險，即使呼叫端目前
        # 沒有已知的重複呼叫路徑，這裡仍加上防呆，不能只靠呼叫端小心。
        conn.close()
        raise ValueError(f"position_id={position_id} 狀態為'{pos['status']}',不是'open',拒絕重複平倉")

    margin_used = pos["avg_cost"] * pos["quantity"] / pos["leverage"]
    if liquidated:
        pnl = -margin_used
    elif pos["side"] == "short":
        pnl = (pos["avg_cost"] - price) * pos["quantity"] - costs
    else:
        pnl = (price - pos["avg_cost"]) * pos["quantity"] - costs

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE position SET status='closed' WHERE position_id=?", (position_id,))
    conn.execute(
        "UPDATE portfolio SET current_cash = current_cash + ? WHERE portfolio_id=?",
        (margin_used + pnl, pos["portfolio_id"]),
    )
    conn.execute(
        """INSERT INTO trade_log (portfolio_id, trade_date, action, symbol, quantity, price, pnl, reasoning, created_at)
           VALUES (?, ?, 'close', ?, ?, ?, ?, ?, ?)""",
        (pos["portfolio_id"], trade_date, pos["symbol"], pos["quantity"], price, pnl, reasoning, now),
    )
    conn.commit()
    conn.close()
    return pnl


def get_recent_trades(portfolio_id: str, limit: int = 10) -> list[dict]:
    """回傳這個帳戶最近N筆交易紀錄(open/close/hold_update皆含),由新到舊,
    供AI決策時參考「上次類似情況做過什麼、結果如何」——純DB查詢,不新增
    任何API/AI呼叫成本。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trade_log WHERE portfolio_id=? ORDER BY created_at DESC LIMIT ?",
        (portfolio_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_published_items(source_id: str, limit: int = 50) -> list[dict]:
    """回傳這個來源狀態為'published'(已判定相關且已推播過)的項目,由新到舊,
    供履歷比對功能查詢目前有效的實習職缺清單用——只要'published'不要
    'seeded_historical'/'stale_not_today'等,避免把首次執行閘門省略的舊
    資料或非當日資料誤當成「目前有效」的職缺。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT title, summary, url, published_at FROM item "
        "WHERE source_id=? AND status='published' ORDER BY fetched_at DESC LIMIT ?",
        (source_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_deposits(portfolio_id: str) -> list[dict]:
    """這個帳戶每筆入金(trade_date, amount)，由舊到新；報酬率扣除入金與XIRR用。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT trade_date, price AS amount FROM trade_log WHERE portfolio_id=? AND action='deposit' "
        "ORDER BY created_at", (portfolio_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_symbols_traded_since(portfolio_id: str, since_iso: str) -> set[str]:
    """since_iso(UTC ISO)之後有開倉或平倉的標的。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM trade_log WHERE portfolio_id=? AND created_at >= ? "
        "AND (action LIKE 'open_%' OR action='close')", (portfolio_id, since_iso),
    ).fetchall()
    conn.close()
    return {r["symbol"] for r in rows}


def get_trades_on(portfolio_id: str, trade_date: str) -> list[dict]:
    """這一天的開倉／平倉紀錄(不含入金與舊版的hold_update)，每日摘要用。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT action, symbol, quantity, price, pnl, reasoning FROM trade_log "
        "WHERE portfolio_id=? AND trade_date=? AND (action LIKE 'open_%' OR action='close') ORDER BY created_at",
        (portfolio_id, trade_date),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def deposit_cash(portfolio_id: str, amount: float, trade_date: str, reasoning: str):
    """模擬每月定期定額的「新增現金」動作(2026-08-06新增,供index_dca_engine
    使用)：current_cash直接加上amount,寫入trade_log(action='deposit')留痕，
    供get_recent_trades()判斷「這個月是否已經注入過本月定額」的節流依據。"""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        "UPDATE portfolio SET current_cash = current_cash + ? WHERE portfolio_id=?",
        (amount, portfolio_id),
    )
    conn.execute(
        """INSERT INTO trade_log (portfolio_id, trade_date, action, price, reasoning, created_at)
           VALUES (?, ?, 'deposit', ?, ?, ?)""",
        (portfolio_id, trade_date, amount, reasoning, now),
    )
    conn.commit()
    conn.close()


def upsert_link_health(item_id: str, original_url: str, resolved_url: str | None,
                       link_status: str, http_status: int | None = None,
                       error_message: str | None = None,
                       replacement_reason: str | None = None) -> None:
    """Record link lifecycle without changing the item URL or dedup key."""
    conn = get_conn()
    previous = conn.execute(
        "SELECT consecutive_failures FROM link_health WHERE item_id=?", (item_id,)
    ).fetchone()
    failures = 0 if link_status in {"OK", "REDIRECTED", "REPLACED"} else ((previous[0] if previous else 0) + 1)
    conn.execute(
        """INSERT INTO link_health
           (item_id, original_url, resolved_url, link_status, checked_at, http_status,
            consecutive_failures, error_message, replacement_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_id) DO UPDATE SET
             resolved_url=excluded.resolved_url, link_status=excluded.link_status,
             checked_at=excluded.checked_at, http_status=excluded.http_status,
             consecutive_failures=excluded.consecutive_failures,
             error_message=excluded.error_message, replacement_reason=excluded.replacement_reason""",
        (item_id, original_url, resolved_url, link_status,
         datetime.now(timezone.utc).isoformat(), http_status, failures,
         error_message, replacement_reason),
    )
    conn.commit()
    conn.close()