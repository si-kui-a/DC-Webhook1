"""
resume_matcher.py — 履歷配對/AI修改建議。

兩種模式(使用者2026-07-31確認新增模式2)：
1. DB比對模式：履歷(PDF/DOCX/純文字)比對DB裡台灣實習頻道目前有效的職缺
   (internship_mol來源、狀態'published')，從清單中挑最適合的幾筆。
2. 單一職缺評估模式：履歷連結+特定職缺連結(任意求職網站，不限104)，
   評估這一份履歷跟這一個職缺搭不搭、給針對性修改建議——不查DB職缺
   清單，範圍限定在使用者指定的這一個職缺。

fetch_url_text()是通用網頁文字抽取(純requests+BeautifulSoup，不執行
JS)，用在履歷分享連結(如104「取得連結」功能產生的公開履歷頁)跟任意
職缺頁面。已知限制：若目標網站是純JS渲染的SPA，抓到的文字會是空的或
極少，這不是程式錯誤，是純requests做法的固有限制(比照專案其餘scraper
遇到JS-only網站時的處理原則——不為此另外導入headless browser，成本
過高，見Meta_Dev_Knowledge.md既有的低成本原則)。

低成本設計：只在使用者主動觸發時才呼叫(CLI/Discord bot互動)，不是排程
自動執行，不會累積額外的背景AI消耗——這點跟其餘scrapers/main.py的
「排程批次」架構刻意不同，見docs/resume_matching_references.md的功能
定位。

兩層架構(使用者2026-07-31確認新增Layer 1)：
- Layer 1(本地，免費)：local_rank_postings()用jieba斷詞+關鍵字重疊計分，
  比照scrapers/internship_util.py等既有關鍵字計分模式，先把DB職缺清單
  篩到只剩最相關的少數幾筆，AI只需要對這幾筆做語意判斷，不用整包50筆
  都送進去——直接降低prompt大小/AI額度消耗。resume_health_check()是
  純規則式的履歷健檢(長度/區塊完整性)，同樣不耗AI額度。
- Layer 2(AI，只對Layer 1篩出的候選才呼叫)：match_and_advise()/
  evaluate_specific_match()原有的Gemini語意比對+建議邏輯不變。
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import jieba
import requests
from bs4 import BeautifulSoup
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

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
MAX_URL_TEXT_CHARS = 8000

# Layer 1篩出的候選數量上限——AI只需要在這個小範圍內做語意排序/理由，
# 不需要看到全部50筆(多數明顯不相關)，8筆已足夠讓AI選出最多5個最終建議。
LOCAL_TOP_K = 8
# 斷詞後的token至少要2個字才納入計分——單一中文字大多是虛詞/太籠統
# (的/是/在...)，用長度門檻粗略過濾，不需要額外處理詞性標註。
MIN_TOKEN_LEN = 2

_stopwords_cache: set[str] | None = None


def _load_stopwords() -> set[str]:
    """重用textrank4zh已內建的中文停用詞表(既有依賴，不新增套件)。"""
    global _stopwords_cache
    if _stopwords_cache is not None:
        return _stopwords_cache
    try:
        import textrank4zh
        path = Path(textrank4zh.__file__).parent / "stopwords.txt"
        _stopwords_cache = set(path.read_text(encoding="utf-8").splitlines())
    except Exception as e:
        logger.warning(f"停用詞表載入失敗，改用空集合(僅靠MIN_TOKEN_LEN過濾): {e}")
        _stopwords_cache = set()
    return _stopwords_cache


def _tokenize(text: str) -> set[str]:
    """jieba斷詞+停用詞/短詞過濾，回傳詞彙集合(用於重疊計分，不需要
    詞頻，重複出現的詞對「這份履歷有沒有提到這個技能/領域」的判斷沒有
    額外意義)。"""
    stopwords = _load_stopwords()
    tokens = jieba.cut(text)
    return {
        t.strip() for t in tokens
        if len(t.strip()) >= MIN_TOKEN_LEN and t.strip() not in stopwords
        and not re.fullmatch(r"[\d\W_]+", t.strip())  # 純數字/純符號不算關鍵字
    }


def local_rank_postings(resume_text: str, postings: list[dict], top_k: int = LOCAL_TOP_K) -> list[dict]:
    """Layer 1本地計分：履歷 vs 每筆職缺(title+summary)的斷詞重疊數，
    由高到低排序，只回傳前top_k筆(重疊數<=0的完全不相關職缺直接排除，
    即使因此不足top_k筆也不硬湊)。純本地運算，不呼叫AI。"""
    resume_tokens = _tokenize(resume_text)
    if not resume_tokens:
        return postings[:top_k]

    scored = []
    for posting in postings:
        posting_text = f"{posting.get('title', '')} {posting.get('summary', '') or ''}"
        posting_tokens = _tokenize(posting_text)
        overlap = resume_tokens & posting_tokens
        if overlap:
            scored.append((len(overlap), posting))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [posting for _, posting in scored[:top_k]]


def resume_health_check(resume_text: str) -> list[str]:
    """純規則式履歷健檢(不耗AI額度)：長度是否過短/過長、是否缺教育/技能/
    經歷這幾類標準區塊的關鍵字。回傳觀察清單(可能是空list，代表沒發現
    明顯問題)，不是判定「好」或「壞」，只是提示可能要注意的地方。"""
    notes = []
    length = len(resume_text.strip())
    if length < 150:
        notes.append(f"履歷內容偏短(僅{length}字)，可能缺少足夠細節讓人資判斷經歷。")
    elif length > 5000:
        notes.append(f"履歷內容偏長({length}字)，建議精簡到重點，避免關鍵資訊被淹沒。")

    section_keywords = {
        "教育背景": ["學歷", "教育", "大學", "科系", "系所"],
        "技能": ["技能", "skill", "熟悉", "程式語言", "工具"],
        "經歷": ["經歷", "經驗", "實習", "工作", "專案", "project"],
    }
    text_lower = resume_text.lower()
    for section_name, keywords in section_keywords.items():
        if not any(kw.lower() in text_lower for kw in keywords):
            notes.append(f"沒有偵測到明確的「{section_name}」相關內容，建議確認履歷是否有涵蓋這部分。")

    return notes


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


def fetch_url_text(url: str) -> str:
    """抓取任意網頁並抽取可讀文字(履歷分享連結/職缺頁面通用)。純requests+
    BeautifulSoup,不執行JS——純JS渲染的SPA會抓到空白或極少文字,這是
    已知限制(見本檔案docstring),不在這裡特別處理或報錯,讓呼叫端從
    抽出來的文字長度自行判斷是否要提醒使用者。"""
    resp = requests.get(url, headers=FETCH_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_URL_TEXT_CHARS]


def evaluate_specific_match(resume_text: str, job_text: str) -> dict | None:
    """比對履歷與單一指定職缺(不查DB職缺清單，範圍限定在呼叫端提供的
    這一個職缺內容)，回傳{"fit_summary": str, "resume_advice": str}或
    None(任何失敗情況)。"""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not resume_text or not resume_text.strip() or not job_text or not job_text.strip():
        return None

    resume_text = resume_text.strip()[:MAX_RESUME_CHARS]
    job_text = job_text.strip()[:MAX_RESUME_CHARS]

    prompt = (
        "你是求職顧問。以下是使用者的履歷全文，以及一個特定職缺的頁面內容。"
        "請評估這份履歷跟這個職缺的契合度，具體指出：\n"
        "1. 哪些經歷/技能符合這個職缺的需求(具體對應，不要空泛地說「符合"
        "需求」)\n"
        "2. 哪些地方不足或有落差\n"
        "3. 針對「投這個職缺」，履歷可以怎麼調整(要對應這個職缺的具體要求，"
        "不是泛用建議)\n\n"
        f"履歷全文:\n{resume_text}\n\n"
        f"職缺頁面內容:\n{job_text}\n\n"
        '只回傳JSON,格式:{"fit_summary": "...", "resume_advice": "..."}'
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
        fit_summary = str(parsed.get("fit_summary", "")).strip()
        resume_advice = str(parsed.get("resume_advice", "")).strip()
        if not fit_summary and not resume_advice:
            return None
        return {"fit_summary": fit_summary or None, "resume_advice": resume_advice or None}
    except Exception as e:
        logger.warning(f"gemini單一職缺評估失敗: {e}")
        return None


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
    health_notes = resume_health_check(resume_text)

    postings = db.get_published_items(internship_mol.SOURCE_ID, limit=MAX_POSTINGS)
    if not postings:
        return {"matches": [], "resume_advice": None, "no_postings": True, "health_notes": health_notes}

    # Layer 1本地篩選：只把最相關的少數幾筆送進AI，降低prompt大小/額度
    # 消耗(2026-07-31新增)。完全篩不出任何重疊(履歷用詞跟職缺清單差異
    # 太大)時退回原始清單，避免Layer 1誤判把真正相關的職缺也擋掉。
    ranked_postings = local_rank_postings(resume_text, postings)
    postings = ranked_postings if ranked_postings else postings

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
        return {
            "matches": matches, "resume_advice": advice or None,
            "no_postings": False, "health_notes": health_notes,
        }
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

    if result.get("health_notes"):
        lines.append("\n【履歷健檢(本地規則判斷，非AI)】")
        for note in result["health_notes"]:
            lines.append(f"• {note}")

    return "\n".join(lines)


def format_specific_match_result(result: dict) -> str:
    """把evaluate_specific_match()的回傳值格式化成人類可讀文字，CLI/
    Discord bot共用。"""
    lines = []
    if result.get("fit_summary"):
        lines.append("【契合度評估】")
        lines.append(result["fit_summary"])
    if result.get("resume_advice"):
        lines.append("\n【針對這個職缺的履歷建議】")
        lines.append(result["resume_advice"])
    return "\n".join(lines) or "AI沒有給出具體內容，請稍後再試。"


def main():
    import argparse
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="履歷配對/AI修改建議(CLI)")
    parser.add_argument("resume_path", nargs="?", help="履歷檔案路徑(支援pdf/docx/txt/md)，與--resume-url二選一")
    parser.add_argument("--resume-url", help="履歷分享連結(如104「取得連結」產生的公開履歷網址)，與resume_path二選一")
    parser.add_argument("--job-url", help="指定單一職缺網址——有給這個參數時，改用「單一職缺評估模式」，不查DB職缺清單")
    args = parser.parse_args()

    if not args.resume_path and not args.resume_url:
        parser.error("請提供履歷檔案路徑，或用--resume-url提供履歷連結")
    if args.resume_path and args.resume_url:
        parser.error("resume_path與--resume-url只能擇一")

    try:
        text = fetch_url_text(args.resume_url) if args.resume_url else extract_text(args.resume_path)
    except Exception as e:
        print(f"履歷解析失敗：{e}", file=sys.stderr)
        sys.exit(1)

    if not text.strip():
        print("履歷解析出來是空白內容，請確認檔案本身有文字(不是純掃描圖片PDF/不是純JS渲染的網頁)。", file=sys.stderr)
        sys.exit(1)

    db.init_db()

    if args.job_url:
        try:
            job_text = fetch_url_text(args.job_url)
        except Exception as e:
            print(f"職缺頁面抓取失敗：{e}", file=sys.stderr)
            sys.exit(1)
        if not job_text.strip():
            print("職缺頁面抓出來是空白內容，可能是純JS渲染的網頁，這種目前抓不到內容。", file=sys.stderr)
            sys.exit(1)
        result = evaluate_specific_match(text, job_text)
        if result is None:
            print("AI評估失敗(額度用盡/網路錯誤/回應格式不對)，請稍後再試。", file=sys.stderr)
            sys.exit(1)
        print(format_specific_match_result(result))
        return

    result = match_and_advise(text)
    if result is None:
        print("AI比對失敗(額度用盡/網路錯誤/回應格式不對)，請稍後再試。", file=sys.stderr)
        sys.exit(1)

    print(format_result_text(result))


if __name__ == "__main__":
    main()
