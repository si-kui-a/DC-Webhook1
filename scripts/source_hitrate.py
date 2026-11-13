"""
scripts/source_hitrate.py — 來源命中率（唯一入口；任何「命中率」都用本腳本輸出，不手算）。

命中率 = published / (published + seeded_historical)；'new'（尚未處理）不計。
依類別（scholarship／internship）列各來源與「類別中位數」。

用法:
    python scripts/source_hitrate.py                       # 讀 data.db，列兩個類別
    python scripts/source_hitrate.py --category internship
    python scripts/source_hitrate.py --trial items.json --category scholarship [--gate]
        試跑模式：items.json 為 [{"title":..,"_filter_text":..(可選)}, ...]，
        用正式的過濾管線算候選來源的命中率；不低於「同類別現有來源中位數」
        才 exit 0（否則 exit 1）。--gate 表示像 RICH 那樣跳過關鍵字計分
        （internship 專用）。
"""
import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CATEGORIES = ("scholarship", "internship")
# 樣本太少的來源不納入中位數（一兩筆的 100%／0% 沒有代表性）
MIN_SAMPLES = 5


def source_rates(conn, category):
    """{source_id: (published, seeded)} for one category."""
    rows = conn.execute(
        "SELECT s.source_id, i.status, COUNT(*) FROM source s "
        "JOIN item i ON i.source_id = s.source_id "
        "WHERE s.category = ? AND i.status IN ('published','seeded_historical') "
        "GROUP BY s.source_id, i.status", (category,)).fetchall()
    out = {}
    for sid, status, n in rows:
        pub, seeded = out.get(sid, (0, 0))
        out[sid] = (pub + n, seeded) if status == "published" else (pub, seeded + n)
    return out


def rate(pub, seeded):
    total = pub + seeded
    return pub / total if total else 0.0


def category_median(rates):
    vals = [rate(p, s) for p, s in rates.values() if p + s >= MIN_SAMPLES]
    return statistics.median(vals) if vals else 0.0


def trial_rate(items, category, gate=False):
    """Hit rate of raw candidate items through the production filter pipeline."""
    if not items:
        return 0, 0
    if category == "scholarship":
        from scrapers import scholarship_util as su
        profile = su.load_profile()
        hits = sum(1 for it in items if su.passes_all_filters(it["title"], profile))
    else:
        from scrapers import internship_util as iu

        def ok(it):
            text = it.get("_filter_text") or it["title"]
            if gate:
                return iu.passes_profile_filters(text)
            return iu.is_relevant(text, salary=it.get("_salary_high")) or iu.is_relevant_job_search(text)
        hits = sum(1 for it in items if ok(it))
    return hits, len(items)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[1])
    ap.add_argument("--db", default=str(ROOT / "data.db"))
    ap.add_argument("--category", choices=CATEGORIES)
    ap.add_argument("--trial", metavar="ITEMS_JSON")
    ap.add_argument("--gate", action="store_true")
    args = ap.parse_args(argv)
    if args.trial and not args.category:
        ap.error("--trial 需要 --category")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cats = [args.category] if args.category else list(CATEGORIES)
    medians = {}
    all_rates = {}
    try:
        for cat in cats:
            all_rates[cat] = source_rates(conn, cat)
    finally:
        conn.close()  # Windows cannot delete a db file that is still open
    for cat in cats:
        rates = all_rates[cat]
        medians[cat] = category_median(rates)
        print(f"== {cat}（中位數 {medians[cat]:.1%}，僅計樣本>={MIN_SAMPLES}的來源）")
        for sid, (p, s) in sorted(rates.items()):
            print(f"  {sid:34s} {rate(p, s):6.1%}  published={p} seeded={s}")
    if args.trial:
        items = json.loads(Path(args.trial).read_text(encoding="utf-8"))
        hits, n = trial_rate(items, args.category, args.gate)
        r = hits / n if n else 0.0
        verdict = r >= medians[args.category]
        print(f"== 試跑 {args.trial}: {hits}/{n} = {r:.1%}；中位數 {medians[args.category]:.1%} → "
              f"{'可併入' if verdict else '低於中位數，不併入'}")
        return 0 if verdict else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
