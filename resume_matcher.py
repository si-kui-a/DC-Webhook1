"""
resume_matcher.py — 履歷配對/AI修改建議。

使用者提供履歷(PDF/DOCX/純文字)，比對DB裡台灣實習頻道目前有效的職缺
(internship_mol來源、狀態'published'——已通過keyword+AI雙層篩選、真的
推播過的項目，不是未篩選的原始快照)，用Gemini給出：
1. 最匹配的職缺排名+理由
2. 履歷本身的具體修改建議

低成本設計：只在使用者主動觸發時才呼叫(CLI/Discord bot互動)，不是排程
自動執行，不會累積額外的背景AI消耗——這點跟其餘scrapers/main.py的
「排程批次」架構刻意不同，見docs/resume_matching_references.md的功能
定位。
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# 跟main.py不同,resume_matcher.py是獨立CLI/bot互動入口,不是透過main.py
# 執行,必須自己載入.env——main.py匯入的其餘模組(如ai_insight.py)不需要
# 自己呼叫load_dotenv()是因為main.py本身在匯入任何東西之前就先呼叫過了。
load_dotenv()

import db
from scrapers import internship_mol

logger = logging.getLogger("resume_matcher")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
TIMEOUT_SECONDS = 30

# 避免超長履歷(含雜訊/重複格式化字元)把prompt灌爆，前6000字已足夠代表
# 履歷主要內容(比照ai_insight.py其餘函式對輸入長度的節制原則)。
MAX_RESUME_CHARS = 6000
# 比照scrapers/us_customer_feeds.py的容量考量，目前published職缺數量少
# (實測10筆),50這個上限留有餘裕，不會不夠用。
MAX_POSTINGS = 50


def _with_retry(fn):
    """對429/5xx/網路錯誤重試,4xx等客戶端錯誤直接往外拋——比照ai_insight.py
    的既有模式，各模組各自持有一份(專案既有慣例，見price_feed.py
    ._get_with_retry())，不跨模組import private helper。"""
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


def extract_text(file_path: str) -> str:
    """依副檔名解析履歷檔案為純文字。不支援的格式/解析失敗直接拋出例外，
    呼叫端(CLI/bot)自行決定如何呈現錯誤訊息給使用者，這裡不吞例外——
    履歷解析失敗使用者一定要知道，不能靜默退化。"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到檔案：{file_path}")
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        import pypdf
        reader = pypdf.PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        import docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")

    raise ValueError(f"不支援的履歷格式：{suffix}（支援pdf/docx/txt/md）")


def match_and_advise(resume_text: str) -> dict | None:
    """
    輸入履歷純文字，回傳
    {"matches": [{"title":str,"url":str,"reason":str}, ...],
     "resume_advice": str|None, "no_postings": bool}
    或None(Gemini呼叫失敗/回應格式不對等任何失敗情況)。
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not resume_text or not resume_text.strip():
        return None

    resume_text = resume_text.strip()[:MAX_RESUME_CHARS]

    postings = db.get_published_items(internship_mol.SOURCE_ID, limit=MAX_POSTINGS)
    if not postings:
        return {"matches": [], "resume_advice": None, "no_postings": True}

    listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p.get('summary') or ''}"
        for i, p in enumerate(postings)
    )

    prompt = (
        "你是求職顧問。以下是使用者的履歷全文，以及目前台灣實習頻道收錄的"
        "有效實習職缺清單。請完成兩件事：\n"
        "1. 從職缺清單中挑出最適合這份履歷的職缺(最多5個)，依適合度排序，"
        "並各自說明為什麼適合(具體對應履歷裡的哪些經歷/技能，不要空泛地說"
        "「符合需求」)。\n"
        "2. 針對這份履歷本身，給出具體、可執行的修改建議(指出具體哪一段"
        "可以怎麼改寫、缺少什麼常見的ATS關鍵字，不要只說「加強實習經驗」"
        "這種空泛的話)。\n\n"
        f"履歷全文:\n{resume_text}\n\n"
        f"目前實習職缺清單:\n{listing}\n\n"
        '只回傳JSON,格式:{"match_indices": [0, 2, ...], '
        '"match_reasons": ["...", "..."], "resume_advice": "..."}'
        "(match_indices與match_reasons按同樣順序、一一對應，"
        "最多5筆，全部不適合就回傳空陣列)"
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
        indices = parsed.get("match_indices", [])
        reasons = parsed.get("match_reasons", [])
        advice = str(parsed.get("resume_advice", "")).strip()
        if not isinstance(indices, list):
            return None

        matches = []
        for i, idx in enumerate(indices):
            if not isinstance(idx, int) or not (0 <= idx < len(postings)):
                continue
            matches.append({
                "title": postings[idx]["title"],
                "url": postings[idx]["url"],
                "reason": reasons[i] if i < len(reasons) else "",
            })
        return {"matches": matches, "resume_advice": advice or None, "no_postings": False}
    except Exception as e:
        logger.warning(f"gemini履歷比對失敗: {e}")
        return None


def format_result_text(result: dict) -> str:
    """把match_and_advise()的回傳值格式化成人類可讀文字，CLI/Discord bot
    共用同一份格式邏輯，避免兩處格式各寫一次容易不同步。"""
    if result.get("no_postings"):
        return "目前DB裡沒有任何有效的實習職缺可比對(可能還沒跑過台灣實習頻道)，僅提供履歷本身的一般建議可能不準，請稍後再試。"

    lines = []
    matches = result.get("matches") or []
    if matches:
        lines.append("【最適合的職缺】")
        for i, m in enumerate(matches, 1):
            lines.append(f"{i}. {m['title']}\n   {m['reason']}\n   {m['url']}")
    else:
        lines.append("【最適合的職缺】目前清單中沒有明顯適合的職缺。")

    if result.get("resume_advice"):
        lines.append("\n【履歷修改建議】")
        lines.append(result["resume_advice"])

    return "\n".join(lines)


def main():
    import argparse
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="履歷配對/AI修改建議(CLI)")
    parser.add_argument("resume_path", help="履歷檔案路徑(支援pdf/docx/txt/md)")
    args = parser.parse_args()

    try:
        text = extract_text(args.resume_path)
    except Exception as e:
        print(f"履歷解析失敗：{e}", file=sys.stderr)
        sys.exit(1)

    if not text.strip():
        print("履歷解析出來是空白內容，請確認檔案本身有文字(不是純掃描圖片PDF)。", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    result = match_and_advise(text)
    if result is None:
        print("AI比對失敗(額度用盡/網路錯誤/回應格式不對)，請稍後再試。", file=sys.stderr)
        sys.exit(1)

    print(format_result_text(result))


if __name__ == "__main__":
    main()
