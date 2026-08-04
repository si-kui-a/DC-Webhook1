"""
select_highlights.py — 從逐字稿(帶時間戳)挑幾個「重要環節」給video_digest.py
截圖用。跟ip根目錄ai_insight.py同一套Gemini呼叫慣例(urllib+_with_retry+
responseMimeType application/json)，這裡不import ai_insight.py本體——那支
模組在root venv、這裡是voice_transcript獨立venv，兩邊套件不共通(見
discord_push.py開頭的同類說明)，照抄同一套模式重寫一份比跨venv import乾淨。

使用者明確要求這一步用AI判斷(不是現成的TextRank/音量峰值訊號)——這是唯一
的「猜哪裡重要」語意判斷，其餘環節(逐字稿/摘要/截圖/PDF/推播)全部維持
0AI，只有這一小段窄範圍呼叫，符合「非結構化語意判斷才用AI」的既有原則。
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# GEMINI_API_KEY在ip根目錄.env，不是voice_transcript自己這層——跟
# discord_push.py的_load_root_env()同一個理由(獨立venv不會自動繼承)。
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
TIMEOUT_SECONDS = 30


def _with_retry(fn):
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


def select_highlights(segments: list[dict], max_highlights: int = 5) -> list[dict]:
    """segments: transcribe_segments()的輸出([{"start","end","text"},...])。
    回傳 [{"timestamp": 秒, "reason": "一句話說明重要在哪"}, ...]，失敗
    (沒有API key/呼叫出錯/回應格式不對)回傳空list——呼叫端視為「這支影片
    沒有AI挑出的重點時刻」，PDF/推播仍然照樣出，只是少了截圖這個環節，
    不會讓整支影片的處理中斷。"""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not segments:
        return []

    numbered = "\n".join(f"[{i}] ({s['start']:.1f}s) {s['text']}" for i, s in enumerate(segments))
    prompt = (
        f"以下是一段影片的逐字稿，已依時間順序切成多個片段，每行開頭是片段"
        f"編號跟起始秒數。請挑出最多{max_highlights}個「重要環節」——內容"
        f"上的轉折、關鍵結論、重要數字/決定，不是單純語氣強調。\n\n"
        f"{numbered}\n\n"
        '只回傳JSON，格式:{"highlights": [{"segment_index": 片段編號(整數), '
        '"reason": "一句話說明這裡重要在哪"}]}，依重要性或時間順序都可以，'
        "找不到明顯重點就回傳空list，不要硬湊數量。"
    )

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
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
        raw_highlights = parsed.get("highlights", [])
    except Exception as e:
        print(f"[select_highlights] AI呼叫或解析失敗，跳過截圖環節：{e}")
        return []

    results = []
    for h in raw_highlights:
        idx = h.get("segment_index")
        reason = h.get("reason", "")
        if not isinstance(idx, int) or not (0 <= idx < len(segments)):
            continue
        results.append({"timestamp": segments[idx]["start"], "reason": reason})
    return results
