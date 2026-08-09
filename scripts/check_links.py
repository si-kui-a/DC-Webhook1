"""Check recently fetched item links without rewriting original URLs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
from scrapers.link_health import check_url, is_allowed_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT item_id, url FROM item WHERE fetched_at >= ? ORDER BY fetched_at DESC LIMIT ?",
        (cutoff, args.limit),
    ).fetchall()
    conn.close()
    counts = {}
    for row in rows:
        url = row["url"]
        if not is_allowed_url(url):
            result = {"status": "NOT_FOUND", "resolved_url": None, "http_status": None, "error": "invalid URL"}
        else:
            result = check_url(url)
        db.upsert_link_health(row["item_id"], url, result.get("resolved_url"), result["status"], result.get("http_status"), result.get("error"))
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())