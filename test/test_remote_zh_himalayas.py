"""中文母語遠端(Himalayas)來源的地點過濾與正規化單元測試(2026-11-14)。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import remote_zh_himalayas as hz  # noqa: E402


class LocationOk(unittest.TestCase):
    def test_kept(self):
        for r in ([], None, ["Taiwan"], ["Germany", "France"], ["Worldwide"], ["Hong Kong", "Taiwan"]):
            self.assertTrue(hz.location_ok(r), r)

    def test_dropped(self):
        for r in (["United States"], ["India"], ["China", "Singapore"]):
            self.assertFalse(hz.location_ok(r), r)


class Normalize(unittest.TestCase):
    def test_fields(self):
        item = hz._normalize({
            "title": "Chinese Interpreter", "companyName": "Acme",
            "applicationLink": "https://himalayas.app/jobs/x",
            "employmentType": "Contractor", "locationRestrictions": ["Taiwan"],
        })
        self.assertEqual(item["title"], "Acme - Chinese Interpreter")
        self.assertEqual(item["url"], "https://himalayas.app/jobs/x")
        self.assertIn("Taiwan", item["summary"])

    def test_missing_link_dropped(self):
        self.assertIsNone(hz._normalize({"title": "x", "applicationLink": ""}))


if __name__ == "__main__":
    unittest.main()
