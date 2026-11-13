"""獎學金過濾（scholarship_util.filter_items）的驗收案例測試。

案例在 config/scholarship_guard_cases.json。改排除詞前先加案例，再跑：
    python -m unittest test.test_scholarship_exclude_regressions
測的是真實設定檔（不是暫存檔），所以排除詞寫太寬（例如子字串 stem 擋掉 system）會在這裡失敗。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import scholarship_util as su  # noqa: E402

with open(os.path.join(ROOT, "config", "scholarship_guard_cases.json"), encoding="utf-8") as _f:
    CASES = json.load(_f)


def passes(title: str) -> bool:
    return bool(su.filter_items([{"title": title}]))


class ScholarshipExcludeCases(unittest.TestCase):
    def setUp(self):
        su.invalidate_cache()

    def test_must_exclude(self):
        for title in CASES["must_exclude"]:
            self.assertFalse(passes(title), title)

    def test_must_keep(self):
        for title in CASES["must_keep"]:
            self.assertTrue(passes(title), title)


if __name__ == "__main__":
    unittest.main()
