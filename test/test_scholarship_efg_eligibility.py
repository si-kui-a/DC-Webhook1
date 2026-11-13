"""EFG 詳細頁資格解析與過濾。用合成 HTML，不連網。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import scholarship_efg as e  # noqa: E402

PROFILE = {"country": "Taiwan"}


def page(*items):
    lis = "".join(f"<li><strong>{k}:</strong> {v}</li>" for k, v in items)
    return f"<h2>Who can apply</h2><ul>{lis}</ul><h2>How to apply</h2>"


class EfgEligibility(unittest.TestCase):
    def test_parse_fields(self):
        f = e.parse_eligibility(page(("Nationality", "All nationalities can apply."), ("Field of study", "Any subject.")))
        self.assertEqual(f["nationality"], "All nationalities can apply.")
        self.assertEqual(f["field of study"], "Any subject.")

    def test_missing_section_is_unknown_and_passes(self):
        f = e.parse_eligibility("<p>nothing</p>")
        self.assertEqual(f, {})
        self.assertTrue(e.is_eligible(f, PROFILE))

    def test_any_nationality_passes(self):
        self.assertTrue(e.is_eligible({"nationality": "Any nationality can apply."}, PROFILE))

    def test_other_country_rejected(self):
        self.assertFalse(e.is_eligible({"nationality": "Citizens of Greece or Cyprus."}, PROFILE))

    def test_taiwan_listed_passes(self):
        self.assertTrue(e.is_eligible({"nationality": "Students from Taiwan, Japan."}, PROFILE))

    def test_master_only_rejected(self):
        self.assertFalse(e.is_eligible({"degree level": "Full-time Masters or PhD."}, PROFILE))

    def test_bachelor_and_master_passes(self):
        self.assertTrue(e.is_eligible({"degree level": "Bachelors or Masters at NTU."}, PROFILE))


class KnownUrls(unittest.TestCase):
    def test_known_urls_only_this_source(self):
        import tempfile
        from pathlib import Path
        import db
        old = db.DB_PATH
        with tempfile.TemporaryDirectory() as d:
            db.DB_PATH = Path(d) / "t.db"
            try:
                db.init_db()
                db.upsert_source("a", "A", "scholarship", "")
                db.upsert_source("b", "B", "scholarship", "")
                db.insert_item_if_new("a", "t1", None, "http://x/1", None)
                db.insert_item_if_new("b", "t2", None, "http://x/2", None)
                self.assertEqual(db.known_urls("a"), {"http://x/1"})
            finally:
                db.DB_PATH = old


if __name__ == "__main__":
    unittest.main()
