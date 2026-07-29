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
