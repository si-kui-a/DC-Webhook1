"""
test_resume_bot_commands.py — resume_bot.py新增的排除/關鍵字自行迭代
指令(2026-08-30)。regex比對+_handle_keyword_command的dispatch邏輯，
internship_util的實際讀寫函式一律monkeypatch，不碰真實config檔案。
執行:python -m unittest discover -s test -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("RESUME_BOT_CHANNEL_ID", "123")

# resume_bot.py呼叫load_dotenv()是模組匯入時的頂層副作用——若真的執行,
# 會把本機真實.env的值(如RENTAL_MIN_MONTHLY)洩漏進這個pytest process的
# os.environ,汙染同一輪跑的其他測試檔(2026-08-30實測發現:這支測試檔
# 一旦跟tests/test_rental_search.py同一輪執行,會讓後者的「未指定門檻應
# 該用test明確傳入的參數,不該被其他值影響」假設失效,兩個原本會過的
# 測試轉為失敗)。這裡把load_dotenv()短路成no-op,匯入resume_bot後才
# 還原,避免這個匯入期副作用外溢到其他測試檔。
with patch("dotenv.load_dotenv"):
    import resume_bot as rb  # noqa: E402


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def _run(coro):
    return asyncio.run(coro)


class RegexOrderingTests(unittest.TestCase):
    """驗證^錨定確實避免"排除："這個子字串在"移除排除："裡被誤判命中
    (見resume_bot.py _handle_keyword_command docstring「實作順序陷阱」)。"""

    def test_add_exclude_does_not_match_remove_exclude_message(self):
        self.assertIsNone(rb._ADD_EXCLUDE_RE.match("移除排除：測試詞"))
        self.assertIsNotNone(rb._REMOVE_EXCLUDE_RE.match("移除排除：測試詞"))

    def test_add_weight_does_not_match_remove_weight_message(self):
        self.assertIsNone(rb._ADD_WEIGHT_RE.match("移除關鍵字：測試詞"))
        self.assertIsNotNone(rb._REMOVE_WEIGHT_RE.match("移除關鍵字：測試詞"))

    def test_plain_chat_matches_nothing(self):
        text = "這是一段普通對話不是指令，超過一百字的話應該要走履歷比對流程" * 3
        self.assertIsNone(rb._ADD_EXCLUDE_RE.match(text))
        self.assertIsNone(rb._REMOVE_EXCLUDE_RE.match(text))
        self.assertIsNone(rb._ADD_WEIGHT_RE.match(text))
        self.assertIsNone(rb._REMOVE_WEIGHT_RE.match(text))

    def test_weight_command_parses_optional_weight(self):
        m = rb._ADD_WEIGHT_RE.match("關鍵字：測試詞 5")
        self.assertEqual(m.groups(), ("測試詞", "5"))
        m2 = rb._ADD_WEIGHT_RE.match("關鍵字：測試詞")
        self.assertEqual(m2.groups(), ("測試詞", None))


class HandleKeywordCommandTests(unittest.TestCase):
    def test_add_exclude_dispatches_and_replies(self):
        ch = FakeChannel()
        with patch.object(rb.internship_util, "add_exclude_keyword") as add_fn, \
             patch.object(rb.internship_util, "invalidate_cache") as inv_fn:
            handled = _run(rb._handle_keyword_command(ch, "排除：測試詞"))
        self.assertTrue(handled)
        add_fn.assert_called_once_with("測試詞")
        inv_fn.assert_called_once()
        self.assertIn("測試詞", ch.sent[0])

    def test_remove_exclude_existing_vs_missing_reply_differs(self):
        ch1 = FakeChannel()
        with patch.object(rb.internship_util, "remove_exclude_keyword", return_value=True), \
             patch.object(rb.internship_util, "invalidate_cache"):
            _run(rb._handle_keyword_command(ch1, "移除排除：詞A"))
        self.assertIn("已從排除清單移除", ch1.sent[0])

        ch2 = FakeChannel()
        with patch.object(rb.internship_util, "remove_exclude_keyword", return_value=False), \
             patch.object(rb.internship_util, "invalidate_cache"):
            _run(rb._handle_keyword_command(ch2, "移除排除：詞B"))
        self.assertIn("本來就不在", ch2.sent[0])

    def test_add_weight_uses_default_when_omitted(self):
        ch = FakeChannel()
        with patch.object(rb.internship_util, "add_weight_keyword") as add_fn, \
             patch.object(rb.internship_util, "invalidate_cache"), \
             patch.object(rb.internship_util, "DEFAULT_KEYWORD_WEIGHT", 2):
            _run(rb._handle_keyword_command(ch, "關鍵字：測試詞"))
        add_fn.assert_called_once_with("測試詞", 2)

    def test_add_weight_uses_explicit_value(self):
        ch = FakeChannel()
        with patch.object(rb.internship_util, "add_weight_keyword") as add_fn, \
             patch.object(rb.internship_util, "invalidate_cache"):
            _run(rb._handle_keyword_command(ch, "關鍵字：測試詞 7"))
        add_fn.assert_called_once_with("測試詞", 7)

    def test_non_command_returns_false_and_sends_nothing(self):
        ch = FakeChannel()
        handled = _run(rb._handle_keyword_command(ch, "今天天氣真好"))
        self.assertFalse(handled)
        self.assertEqual(ch.sent, [])


if __name__ == "__main__":
    unittest.main()
