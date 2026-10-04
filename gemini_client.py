"""gemini_client.py — 唯一一份Gemini generateContent呼叫＋重試邏輯。

原本ai_insight.py(3份)與resume_matcher.py(2份＋一份自己的_with_retry)各自
照抄同一段urllib請求，改模型名稱或重試規則要同步改5處(2026-10-04整併)。
voice_transcript/有自己的venv，刻意不import這支，維持它自己的副本。

Usage(程式內呼叫，不是指令):
    import gemini_client
    text = gemini_client.call_json_text(prompt, temperature=0.3, timeout=30)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

MODEL_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
RETRYABLE_HTTP = (429, 500, 502, 503, 504)


def with_retry(fn):
    """對429/5xx/網路錯誤重試(指數退避)，其他4xx(如API key無效)直接往外拋
    ——重試也不會變好，白等徒增延遲。"""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code not in RETRYABLE_HTTP or attempt == MAX_RETRIES - 1:
                raise
        except urllib.error.URLError as e:
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                raise
        time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
    raise last_exc  # pragma: no cover


def call_json_text(prompt: str, temperature: float = 0.3, timeout: int = 30) -> str:
    """送出prompt(要求JSON回應)，回傳模型原始文字(未parse)。失敗原樣往外拋
    (HTTPError/URLError/KeyError)，由呼叫端決定要降級還是切備援供應商。"""
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        MODEL_URL, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
        method="POST",
    )

    def _do():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    data = with_retry(_do)
    return data["candidates"][0]["content"]["parts"][0]["text"]
