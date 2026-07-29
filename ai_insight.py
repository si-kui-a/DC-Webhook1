"""
ai_insight.py — fed/tsmc（英文機構新聞稿）專用的Gemini免費層輔助：中文翻譯+
利多利空判斷。純requests呼叫,不新增任何套件依賴。

設計原則(呼應summarizer_en.py/summarizer_zh.py既有的「單筆失敗不中斷整體
推播」原則):
- 任何一步失敗(額度用盡/網路錯誤/回應格式不對)一律回傳None,呼叫端
  (main.py)沿用「無AI內容,退回既有抽取式摘要」的邏輯。
- 只在main.py確定「這筆真的會被推播」之後才呼叫,不對首次執行安全閘門
  擋下的歷史項目呼叫,避免浪費免費額度。
- cbc本來就是中文來源,不適用此模組(呼叫端只在key in ('fed','tsmc')時
  才呼叫)。
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("ai_insight")

ALLOWED_SENTIMENTS = ("利多", "利空", "中性")
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
TIMEOUT_SECONDS = 30


def _with_retry(fn):
    """對429/5xx/網路錯誤重試,4xx等客戶端錯誤(如API key無效)不重試,
    直接往外拋——重試也不會變好,白等徒增延遲。"""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES - 1:
                raise
        except urllib.error.URLError as e:
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                raise
        time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    raise last_exc  # pragma: no cover


def get_translation_and_sentiment(english_summary: str) -> dict | None:
    """
    輸入已由summarizer_en.py抽取式摘要出的英文重點句(LexRank已挑出3句內、
    250字內的關鍵句子,不是完整正文——避免為了餵給Gemini而重新對detail頁
    多發一次HTTP請求),回傳
    {"zh_summary": str, "sentiment": "利多"|"利空"|"中性", "sentiment_reason": str}
    或None(任何失敗情況)。
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not english_summary or not english_summary.strip():
        return None

    prompt = (
        "以下是一則機構(聯準會或台積電)英文新聞稿裡,已用抽取式演算法挑出的"
        "重點句(不是完整正文,只有最關鍵的幾句),供台灣投資人參考。\n"
        "請完成兩件事:\n"
        "1. 用繁體中文(台灣用語)翻譯並精簡整理成一段摘要,150字以內。\n"
        "2. 依這些重點句判斷這則新聞對股市/總經情勢屬於「利多」「利空」還是"
        "「中性」,只能三選一,並給一句話(30字以內)的判斷理由。\n"
        "只能根據提供的內容判斷,不可揣測或補充未載明的資訊。\n"
        '只回傳JSON,格式:{"zh_summary": "...", "sentiment": "...", "sentiment_reason": "..."}\n\n'
        "重點句:\n" + english_summary
    )
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    def _do():
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = _with_retry(_do)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        if parsed.get("sentiment") not in ALLOWED_SENTIMENTS:
            logger.warning(f"gemini回傳不合法的sentiment: {parsed.get('sentiment')!r}")
            return None
        zh_summary = str(parsed.get("zh_summary", "")).strip()
        if not zh_summary:
            return None
        return {
            "zh_summary": zh_summary[:250],
            "sentiment": parsed["sentiment"],
            "sentiment_reason": str(parsed.get("sentiment_reason", "")).strip()[:60],
        }
    except Exception as e:
        logger.warning(f"gemini翻譯/情緒判斷失敗: {e}")
        return None
