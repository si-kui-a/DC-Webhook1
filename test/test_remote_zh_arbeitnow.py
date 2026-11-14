"""Arbeitnow 實習篩選與中文權重單元測試(2026-11-14)。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import remote_zh_arbeitnow as an  # noqa: E402


def job(title="x", types=(), desc="", tags=(), **kw):
    return {"title": title, "job_types": list(types), "description": desc, "tags": list(tags),
            "url": "https://www.arbeitnow.com/jobs/" + title.replace(" ", "-"), **kw}


class IsIntern(unittest.TestCase):
    def test_kept(self):
        for j in (job("Praktikum Marketing"), job("Data Intern"), job("Werkstudent (m/w/d)"),
                  job("Analyst", types=["Intern"]), job("Master Thesis AI")):
            self.assertTrue(an.is_intern(j), j["title"])

    def test_dropped(self):
        # intern 不能命中 international / internal
        for j in (job("International Sales Manager"), job("Internal Auditor"), job("Software Engineer", types=["Full Time"])):
            self.assertFalse(an.is_intern(j), j["title"])


class ChineseWeight(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(an.chinese_weight(job("Praktikum Chinese Market")), 2)
        self.assertEqual(an.chinese_weight(job("Praktikum", desc="Mandarin is a plus")), 1)
        self.assertEqual(an.chinese_weight(job("Praktikum", desc="German only")), 0)


class Normalize(unittest.TestCase):
    def test_chinese_tag_and_remote(self):
        item = an._normalize(job("Praktikum Mandarin", company_name="Acme", remote=True))
        self.assertTrue(item["title"].startswith("中文 Acme - "))
        self.assertIn("全遠端", item["summary"])

    def test_missing_url_dropped(self):
        self.assertIsNone(an._normalize({"title": "x", "url": ""}))


class Registered(unittest.TestCase):
    def test_in_remote_zh_job(self):
        from jobs.remote_zh import REMOTE_ZH_SOURCES
        self.assertIn("remote_zh_arbeitnow", REMOTE_ZH_SOURCES)
        self.assertIs(REMOTE_ZH_SOURCES["remote_zh_arbeitnow"][0], an.fetch)


class FetchOrder(unittest.TestCase):
    def test_chinese_first_and_non_intern_dropped(self):
        pages = [{"data": [job("Praktikum A"), job("Sales Manager"), job("Praktikum B", desc="Chinese")]}, {"data": []}]
        calls = iter(pages)

        class Resp:
            def __init__(self, d):
                self.d = d

            def json(self):
                return self.d

        orig_req, orig_sleep = an.request, an.time.sleep
        an.request, an.time.sleep = (lambda *a, **k: Resp(next(calls))), (lambda s: None)
        try:
            items = an.fetch()
        finally:
            an.request, an.time.sleep = orig_req, orig_sleep
        self.assertEqual([i["title"] for i in items], ["中文 Praktikum B", "Praktikum A"])


if __name__ == "__main__":
    unittest.main()
