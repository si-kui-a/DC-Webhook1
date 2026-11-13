"""source_hitrate: rate/median arithmetic on a synthetic sqlite, plus trial verdict."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import source_hitrate as sh


def _db(rows):
    """rows: [(source_id, category, status, count)]"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE source (source_id TEXT, category TEXT)")
    conn.execute("CREATE TABLE item (source_id TEXT, status TEXT)")
    for sid, cat, status, n in rows:
        if not conn.execute("SELECT 1 FROM source WHERE source_id=?", (sid,)).fetchone():
            conn.execute("INSERT INTO source VALUES (?,?)", (sid, cat))
        conn.executemany("INSERT INTO item VALUES (?,?)", [(sid, status)] * n)
    conn.commit()  # backup() blocks on an open write transaction
    return conn


class RateTests(unittest.TestCase):
    def test_new_status_and_other_category_ignored(self):
        conn = _db([("a", "scholarship", "published", 3), ("a", "scholarship", "seeded_historical", 7),
                    ("a", "scholarship", "new", 50), ("b", "internship", "published", 9)])
        self.assertEqual(sh.source_rates(conn, "scholarship"), {"a": (3, 7)})

    def test_median_skips_tiny_samples(self):
        rates = {"a": (5, 5), "b": (3, 3), "c": (9, 1), "tiny": (1, 0)}  # 50%, 50%, 90%; tiny skipped
        self.assertAlmostEqual(sh.category_median(rates), 0.5)
        self.assertEqual(sh.category_median({"tiny": (1, 0)}), 0.0)

    def test_trial_cli_verdict(self):
        conn_rows = [("a", "scholarship", "published", 5), ("a", "scholarship", "seeded_historical", 5)]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "t.db"
            disk = sqlite3.connect(db)
            _db(conn_rows).backup(disk)
            disk.close()
            items = Path(d) / "items.json"
            items.write_text(json.dumps([{"title": "x"}]), encoding="utf-8")
            orig = sh.trial_rate
            try:
                sh.trial_rate = lambda i, c, g=False: (4, 5)  # 80% >= 50%
                self.assertEqual(sh.main(["--db", str(db), "--category", "scholarship", "--trial", str(items)]), 0)
                sh.trial_rate = lambda i, c, g=False: (1, 5)  # 20% < 50%
                self.assertEqual(sh.main(["--db", str(db), "--category", "scholarship", "--trial", str(items)]), 1)
            finally:
                sh.trial_rate = orig


if __name__ == "__main__":
    unittest.main()
