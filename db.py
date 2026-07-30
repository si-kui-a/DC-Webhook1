"""
db.py — SQLite 存取層。
不用 ORM：資料量小（週報/月報頻率），直接 sqlite3 標準庫即可，減少依賴。
"""
import sqlite3
import hashlib
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


def make_dedup_key(source_id: str, title: str, url: str) -> str:
    """去重鍵：來源 + 標題 + URL 正規化後雜湊。標題正規化可避免空白/全半形差異造成重複推播。"""
    normalized = f"{source_id}|{title.strip()}|{url.strip()}"
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
    既有的item.summary，見main.py run_meta_summary_channel)。多筆符合時
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


def close_position(position_id: str, price: float, trade_date: str, reasoning: str) -> float:
    """平倉：歸還margin_used+pnl給current_cash,回傳這筆realized_pnl。"""
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
    if pos["side"] == "short":
        pnl = (pos["avg_cost"] - price) * pos["quantity"]
    else:
        pnl = (price - pos["avg_cost"]) * pos["quantity"]

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE position SET status='closed' WHERE position_id=?", (position_id,))
    conn.execute(
        "UPDATE portfolio SET current_cash = current_cash + ? WHERE portfolio_id=?",
        (margin_used + pnl, pos["portfolio_id"]),
    )
    # pnl欄位2026-07-31新增(ALTER TABLE，待使用者APPROVED後才會真的存在)，
    # 用try/except容錯：欄位還沒建立前，退化成不存pnl的舊版INSERT，不讓
    # Kelly功能開發卡住其餘平倉邏輯——遷移生效後這個except分支自然不再
    # 觸發，不需要額外開關切換。
    try:
        conn.execute(
            """INSERT INTO trade_log (portfolio_id, trade_date, action, symbol, quantity, price, pnl, reasoning, created_at)
               VALUES (?, ?, 'close', ?, ?, ?, ?, ?, ?)""",
            (pos["portfolio_id"], trade_date, pos["symbol"], pos["quantity"], price, pnl, reasoning, now),
        )
    except sqlite3.OperationalError:
        conn.execute(
            """INSERT INTO trade_log (portfolio_id, trade_date, action, symbol, quantity, price, reasoning, created_at)
               VALUES (?, ?, 'close', ?, ?, ?, ?, ?)""",
            (pos["portfolio_id"], trade_date, pos["symbol"], pos["quantity"], price, reasoning, now),
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


def get_recent_summaries(source_id: str, limit: int = 5) -> list[dict]:
    """回傳這個來源最近N筆summary(依寫入時間由新到舊),供AI比對「這幾天
    報告怎麼變化」的趨勢,不只看單一天的截面——同樣是純DB查詢。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT published_at, summary FROM item WHERE source_id=? "
        "ORDER BY fetched_at DESC LIMIT ?",
        (source_id, limit),
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


def get_trade_win_stats(portfolio_id: str) -> dict | None:
    """回傳這個帳戶歷史已平倉交易的勝率統計(供Kelly公式部位建議用)。
    trade_log.pnl欄位是2026-07-31新增的ALTER TABLE(待APPROVED)，欄位還
    不存在時捕捉OperationalError回傳None，讓呼叫端自然退化成「歷史樣本
    不足」的既有分支，不需要另外判斷欄位是否存在。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT pnl FROM trade_log WHERE portfolio_id=? AND action='close' AND pnl IS NOT NULL",
            (portfolio_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return None
    conn.close()

    pnls = [r["pnl"] for r in rows]
    if not pnls:
        return None

    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "sample_size": len(pnls),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


def record_hold(portfolio_id: str, trade_date: str, reasoning: str):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        """INSERT INTO trade_log (portfolio_id, trade_date, action, reasoning, created_at)
           VALUES (?, ?, 'hold_update', ?, ?)""",
        (portfolio_id, trade_date, reasoning, now),
    )
    conn.commit()
    conn.close()
