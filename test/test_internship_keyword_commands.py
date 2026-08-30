"""
test_internship_keyword_commands.py — internship_util.py的排除/關鍵字
Discord指令讀寫函式(2026-08-30新增，見resume_bot.py「排除：」「關鍵字：」
等指令)。一律對temp檔案操作，絕不碰真實config/internship_keywords.json。
執行:python -m unittest discover -s test -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import internship_util as iu  # noqa: E402


class KeywordCommandTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.path = Path(path)
        self.path.write_text(json.dumps({
            "_comment": "測試設定檔",
            "weights": {"既有詞": 2},
            "exclude_keywords": ["既有排除詞"],
            "threshold": 2,
        }, ensure_ascii=False), encoding="utf-8")
        patcher = patch.object(iu, "_KEYWORDS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.path.unlink)
        self.addCleanup(iu.invalidate_cache)

    def test_add_exclude_keyword(self):
        iu.add_exclude_keyword("新排除詞")
        data = iu._read_raw_config()
        self.assertIn("新排除詞", data["exclude_keywords"])
        self.assertIn("既有排除詞", data["exclude_keywords"])  # 沒有覆蓋掉既有的

    def test_add_exclude_keyword_no_duplicate(self):
        iu.add_exclude_keyword("既有排除詞")
        data = iu._read_raw_config()
        self.assertEqual(data["exclude_keywords"].count("既有排除詞"), 1)

    def test_remove_exclude_keyword_existing(self):
        existed = iu.remove_exclude_keyword("既有排除詞")
        self.assertTrue(existed)
        data = iu._read_raw_config()
        self.assertNotIn("既有排除詞", data["exclude_keywords"])

    def test_remove_exclude_keyword_missing_returns_false(self):
        existed = iu.remove_exclude_keyword("從沒出現過的詞")
        self.assertFalse(existed)

    def test_add_weight_keyword_default_weight(self):
        iu.add_weight_keyword("新關鍵字")
        data = iu._read_raw_config()
        self.assertEqual(data["weights"]["新關鍵字"], iu.DEFAULT_KEYWORD_WEIGHT)

    def test_add_weight_keyword_explicit_weight(self):
        iu.add_weight_keyword("新關鍵字", 5)
        data = iu._read_raw_config()
        self.assertEqual(data["weights"]["新關鍵字"], 5)

    def test_remove_weight_keyword(self):
        existed = iu.remove_weight_keyword("既有詞")
        self.assertTrue(existed)
        data = iu._read_raw_config()
        self.assertNotIn("既有詞", data["weights"])

    def test_comment_fields_preserved_through_write(self):
        iu.add_exclude_keyword("x")
        data = iu._read_raw_config()
        self.assertEqual(data["_comment"], "測試設定檔")
        self.assertEqual(data["threshold"], 2)

    def test_invalidate_cache_makes_score_title_see_new_keyword(self):
        """驗證整條路徑：寫入->invalidate_cache->score_title真的讀到新值,
        不是只測_read_raw_config這個內部函式。"""
        iu.add_weight_keyword("超新關鍵字", 10)
        iu.invalidate_cache()
        self.assertEqual(iu.score_title("職缺標題包含超新關鍵字"), 10)


if __name__ == "__main__":
    unittest.main()
