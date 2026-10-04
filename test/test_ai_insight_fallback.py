"""
test_ai_insight_fallback.py — 驗證ai_insight.py的GPT備援邊界(2026-08-29新增):
- 翻譯/情緒判斷、頻道彙整、大總結:Gemini失敗且符合白名單條件時才切GPT。
不打真實網路,urllib.request.urlopen與provider輔助函式一律monkeypatch/mock。
"""
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ai_insight  # noqa: E402
import gemini_client  # noqa: E402


def _http_error(code):
    return urllib.error.HTTPError(url="https://example.test", code=code, msg="err", hdrs=None, fp=None)


class IsFailoverEligibleTests(unittest.TestCase):
    def test_retryable_and_auth_http_codes_eligible(self):
        for code in (401, 403, 429, 500, 502, 503, 504):
            self.assertTrue(ai_insight._is_failover_eligible(_http_error(code)), code)

    def test_other_http_codes_not_eligible(self):
        for code in (400, 404, 422):
            self.assertFalse(ai_insight._is_failover_eligible(_http_error(code)), code)

    def test_network_error_eligible(self):
        self.assertTrue(ai_insight._is_failover_eligible(urllib.error.URLError("timed out")))

    def test_generic_exception_not_eligible(self):
        self.assertFalse(ai_insight._is_failover_eligible(ValueError("bad json")))


class CallAiJsonTextFallbackTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(gemini_client.time, "sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_primary_success_never_calls_gpt(self):
        with patch.object(ai_insight, "_call_gemini_json_text", return_value="gemini text") as gem, \
             patch.object(ai_insight, "_call_openai_json_text") as gpt:
            result = ai_insight._call_ai_json_text("prompt", temperature=0.2, timeout=30)
        self.assertEqual(result, "gemini text")
        gem.assert_called_once()
        gpt.assert_not_called()

    def test_429_failover_to_gpt_when_key_present(self):
        with patch.object(ai_insight, "_call_gemini_json_text", side_effect=_http_error(429)), \
             patch.object(ai_insight, "_call_openai_json_text", return_value="gpt text") as gpt, \
             patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            result = ai_insight._call_ai_json_text("prompt", temperature=0.2, timeout=30)
        self.assertEqual(result, "gpt text")
        gpt.assert_called_once()

    def test_no_failover_without_openai_key(self):
        with patch.object(ai_insight, "_call_gemini_json_text", side_effect=_http_error(429)), \
             patch.object(ai_insight, "_call_openai_json_text") as gpt, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(urllib.error.HTTPError):
                ai_insight._call_ai_json_text("prompt", temperature=0.2, timeout=30)
        gpt.assert_not_called()

    def test_400_does_not_failover(self):
        with patch.object(ai_insight, "_call_gemini_json_text", side_effect=_http_error(400)), \
             patch.object(ai_insight, "_call_openai_json_text") as gpt, \
             patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                ai_insight._call_ai_json_text("prompt", temperature=0.2, timeout=30)
        self.assertEqual(ctx.exception.code, 400)
        gpt.assert_not_called()

    def test_gpt_also_fails_raises_original_gemini_error(self):
        gemini_exc = _http_error(429)
        with patch.object(ai_insight, "_call_gemini_json_text", side_effect=gemini_exc), \
             patch.object(ai_insight, "_call_openai_json_text",
                           side_effect=RuntimeError("openai HTTP 500")), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                ai_insight._call_ai_json_text("prompt", temperature=0.2, timeout=30)
        self.assertIs(ctx.exception, gemini_exc)


class NonTradeFunctionsIntegrationTests(unittest.TestCase):
    def test_get_translation_and_sentiment_uses_fallback_result(self):
        payload = '{"zh_summary": "摘要", "sentiment": "利多", "sentiment_reason": "理由"}'
        with patch.object(ai_insight, "_call_ai_json_text", return_value=payload), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "gk-test"}):
            result = ai_insight.get_translation_and_sentiment("some english text")
        self.assertEqual(result["sentiment"], "利多")

    def test_get_translation_and_sentiment_returns_none_when_all_fail(self):
        with patch.object(ai_insight, "_call_ai_json_text", side_effect=_http_error(500)), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "gk-test"}):
            result = ai_insight.get_translation_and_sentiment("some english text")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
