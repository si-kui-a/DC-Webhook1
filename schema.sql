-- schema.sql
-- 三張表：source（來源定義）、item（抓取結果）、delivery_log（推播紀錄）
-- 設計原則：item.dedup_key 唯一，避免同一公告被重複推播。

CREATE TABLE IF NOT EXISTS source (
    source_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,          -- institutional / market / integrate
    base_url        TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_fetched_at TEXT,
    fail_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS item (
    item_id         TEXT PRIMARY KEY,       -- uuid
    source_id       TEXT NOT NULL REFERENCES source(source_id),
    title           TEXT NOT NULL,
    summary         TEXT,
    url             TEXT NOT NULL,
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    dedup_key       TEXT NOT NULL UNIQUE,   -- sha256(source_id + normalized_title/url)
    status          TEXT NOT NULL DEFAULT 'new'
    -- new / published / archived / seeded_historical / digest_omitted
    -- seeded_historical：cbc 這類「一次回傳全部歷史」的來源，第一次執行時
    -- 因為首次執行安全閘門（見 main.py FIRST_RUN_PUSH_CAP）只推播其中最新
    -- 幾筆，其餘全部寫入 dedup_key 但從未真正呼叫過 webhook。故意不用 'new'，
    -- 避免將來有 catch-up/replay 邏輯照字面意思去撈「status='new' 代表待
    -- 推播」時，把這批舊資料誤判成待推播而一次噴發。'new' 保留給真正尚未
    -- 處理過、之後應該被推播的項目。
    -- digest_omitted：晚間彙整頻道（ai_insight.build_channel_digest／
    -- digest_format.py）因篇幅（最多2則訊息）裝不下而被省略的重點，跟
    -- seeded_historical 的「首次執行閘門」語意不同，單獨區分。
    -- stale_not_today：晚間彙整頻道要求「只收錄台灣時區當日發佈的文章」
    -- （見 main.py _is_today_in_taiwan），published_at 不是今天的項目，
    -- 跟上面兩種省略原因都不同，單獨區分。
);

CREATE TABLE IF NOT EXISTS delivery_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT,
    channel         TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    http_status     INTEGER,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_item_source ON item(source_id);
CREATE INDEX IF NOT EXISTS idx_item_status ON item(status);
