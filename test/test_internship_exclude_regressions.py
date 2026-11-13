"""
test_internship_exclude_regressions.py — 排除規則的驗收（案例唯一來源：
config/exclude_guard_cases.json）。

背景：2026-09-02 新增基礎勞動力／居家照護清潔／駕駛三類排除詞時，初稿用了
「駕駛」「搬運」「生產線」「保全」「清潔」等產業別裸詞，會誤殺同樣帶到子字串的
半導體／科技職缺（自動駕駛系統工程師等）。改用職稱後綴或完整複合詞後修正，
並把判斷固化成案例。

之後改排除詞：先在 exclude_guard_cases.json 加 must_exclude／must_keep 案例，
再改設定檔，跑本測試即知有沒有誤殺。案例不要寫回這個檔案。
直接讀真實 production config，不 mock 路徑。
用 is_relevant_job_search()（求職頻道公開 API）；案例標題刻意不含「實習」等
關鍵字，才是單純測到行業別排除這一層。

執行:python -m unittest test.test_internship_exclude_regressions -v
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import internship_util as iu  # noqa: E402
from scrapers import thu_calendar  # noqa: E402

with open(os.path.join(ROOT, "config", "exclude_guard_cases.json"), encoding="utf-8") as _f:
    CASES = json.load(_f)


class InternshipExcludeCases(unittest.TestCase):
    def setUp(self):
        iu.invalidate_cache()

    def test_must_exclude(self):
        for title in CASES["internship"]["must_exclude"]:
            self.assertFalse(iu.is_relevant_job_search(title), title)

    def test_must_keep(self):
        for title in CASES["internship"]["must_keep"]:
            self.assertTrue(iu.is_relevant_job_search(title), title)


class ThuCalendarExcludeCases(unittest.TestCase):
    def setUp(self):
        thu_calendar.invalidate_cache()

    def test_must_exclude(self):
        for title in CASES["thu_calendar"]["must_exclude"]:
            self.assertFalse(thu_calendar.is_relevant_to_students(title), title)

    def test_must_keep(self):
        for title in CASES["thu_calendar"]["must_keep"]:
            self.assertTrue(thu_calendar.is_relevant_to_students(title), title)


class AuditScriptTests(unittest.TestCase):
    """scripts/audit_exclude_rules.py 的結構與案例檢查必須通過（重複詞、冗餘詞、
    案例失敗都會在這裡擋下），不必靠人記得手動跑。"""

    def test_audit_structure_and_cases_clean(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "audit_exclude_rules", os.path.join(ROOT, "scripts", "audit_exclude_rules.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lists = mod._term_lists()
        self.assertEqual(mod.check_structure(lists) + mod.check_cases(), [])
        # 救回規則的詞清單也要在稽核範圍內，否則重複或空白詞不會被抓到
        self.assertIn("internship.exclude_rescue[0].unless_also", lists)


if __name__ == "__main__":
    unittest.main()
