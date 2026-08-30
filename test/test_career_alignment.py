"""
test_career_alignment.py — 履歷對齊分析(career_alignment.py，2026-08-30
新增，見memory project-career-repo-scraper-integration-plan)。不打真實
網路，_crawl_role/internship_mol.fetch一律monkeypatch。
執行:python -m unittest discover -s test -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import career_alignment as ca  # noqa: E402


class LoadTargetRolesTests(unittest.TestCase):
    def _write_profile(self, content: str, encoding: str = "utf-8") -> Path:
        d = Path(tempfile.mkdtemp())
        (d / ".git").mkdir()  # is_available()需要，這裡的測試不呼叫is_available但求一致
        (d / "profile.yaml").write_text(content, encoding=encoding)
        return d

    def test_flow_style(self):
        d = self._write_profile('target_roles: ["產品經理", "專案管理師"]\n')
        with patch.object(ca, "CAREER_REPO_DIR", d):
            self.assertEqual(ca.load_target_roles(), ["產品經理", "專案管理師"])

    def test_block_style(self):
        d = self._write_profile('target_roles:\n  - 產品經理\n  - "專案管理師"\n')
        with patch.object(ca, "CAREER_REPO_DIR", d):
            self.assertEqual(ca.load_target_roles(), ["產品經理", "專案管理師"])

    def test_empty_flow_style(self):
        d = self._write_profile("target_roles: []\n")
        with patch.object(ca, "CAREER_REPO_DIR", d):
            self.assertEqual(ca.load_target_roles(), [])

    def test_utf8_bom_does_not_break_parsing(self):
        # 2026-08-30實測發現：PowerShell Out-File -Encoding utf8預設會加
        # BOM，殘留在第一行會讓startswith("target_roles:")比對失敗。
        d = self._write_profile('target_roles: ["A"]\n', encoding="utf-8-sig")
        with patch.object(ca, "CAREER_REPO_DIR", d):
            self.assertEqual(ca.load_target_roles(), ["A"])

    def test_missing_file_returns_empty(self):
        d = Path(tempfile.mkdtemp())
        with patch.object(ca, "CAREER_REPO_DIR", d):
            self.assertEqual(ca.load_target_roles(), [])


class TokenizeTests(unittest.TestCase):
    def test_keeps_nouns_and_english(self):
        tokens = ca._tokenize("我熟悉Python程式設計與資料分析")
        self.assertIn("Python", tokens)
        self.assertTrue(any("分析" in t or "資料" in t for t in tokens))

    def test_filters_boilerplate(self):
        tokens = ca._tokenize("地點：台北市 薪資：面議 股份有限公司")
        for noise in ("地點", "薪資", "股份", "有限公司", "面議"):
            self.assertNotIn(noise, tokens)

    def test_filters_short_and_punctuation(self):
        tokens = ca._tokenize("的 是 ， 。 3")
        self.assertEqual(tokens, set())


class AnalyzeTests(unittest.TestCase):
    def test_no_roles_short_circuits_without_network(self):
        with patch.object(ca, "load_target_roles", return_value=[]), \
             patch.object(ca.internship_mol, "fetch") as mol_fetch, \
             patch.object(ca, "_crawl_role") as crawl:
            result = ca.analyze("履歷內容")
        self.assertTrue(result["no_roles"])
        self.assertEqual(result["roles"], [])
        mol_fetch.assert_not_called()
        crawl.assert_not_called()

    def test_insufficient_sample_flagged(self):
        fake_postings = [{"title": "測試職缺", "summary": "需要Python技能"}] * 3
        with patch.object(ca.internship_mol, "fetch", return_value=[]), \
             patch.object(ca, "_crawl_role", return_value=fake_postings):
            result = ca.analyze("履歷", roles=["稀有職位"])
        role_result = result["roles"][0]
        self.assertEqual(role_result["sample_size"], 3)
        self.assertTrue(role_result["insufficient_sample"])

    def test_gap_words_exclude_resume_tokens(self):
        fake_postings = [
            {"title": f"職缺{i}", "summary": "需要Python與Docker經驗"} for i in range(12)
        ]
        with patch.object(ca.internship_mol, "fetch", return_value=[]), \
             patch.object(ca, "_crawl_role", return_value=fake_postings):
            result = ca.analyze("我熟悉Python開發", roles=["後端工程師"])
        role_result = result["roles"][0]
        self.assertFalse(role_result["insufficient_sample"])
        self.assertNotIn("Python", role_result["gap_words"])  # 履歷裡有，不該是落差詞
        self.assertIn("Docker", role_result["gap_words"])  # 履歷裡沒有，該被抓到

    def test_one_source_failure_does_not_abort_role(self):
        """單一來源(如GIFT分頁404)抓取失敗不中斷整體，其餘來源結果仍照算。"""
        def flaky_fetch(keyword):
            raise RuntimeError("404")

        with patch.object(ca.internship_104, "fetch", side_effect=flaky_fetch), \
             patch.object(ca.internship_518, "fetch", return_value=[{"title": "t", "summary": "Python"}] * 15), \
             patch.object(ca.internship_yes123, "fetch", return_value=[]), \
             patch.object(ca.internship_gift, "fetch", return_value=[]), \
             patch.object(ca.internship_mol, "fetch", return_value=[]):
            result = ca.analyze("履歷", roles=["測試角色"])
        self.assertEqual(result["roles"][0]["sample_size"], 15)


class FormatReportTextTests(unittest.TestCase):
    def test_no_roles_message(self):
        text = ca.format_report_text({"no_roles": True, "roles": []})
        self.assertIn("target_roles目前是空的", text)

    def test_insufficient_sample_warning_included(self):
        result = {
            "no_roles": False,
            "roles": [{
                "role": "測試角色", "sample_size": 2, "insufficient_sample": True,
                "commonly_required": ["Python"], "gap_words": ["Python"],
            }],
        }
        text = ca.format_report_text(result)
        self.assertIn("樣本不足", text)
        self.assertIn("Python", text)


if __name__ == "__main__":
    unittest.main()
