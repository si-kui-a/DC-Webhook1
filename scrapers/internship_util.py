"""
scrapers/internship_util.py — 台灣實習頻道關鍵字計分工具。

比照scholarship_util.py的計分模式(score_title/is_relevant)。

2026-07-31修正:先前(2026-07-30)因「科系與資歷是通用的」決定不套用
scholarship_util.py那套學校/年級/國籍/身份別排除規則——使用者已推翻此決定,
改為「科系與資歷應該要設限」。學校/年級/國籍/身份別直接重用
scholarship_util.py既有函式(同一份config/scholarship_profile.json,同一個
人的條件,不另開設定檔);科系是新增維度(見is_excluded_by_major,
scholarship_util沒有這個,獎學金較少寫死指定科系,實習職缺常見「限OO
相關科系」)。
"""
import json
import logging
import os
import re
from pathlib import Path

from scrapers import scholarship_util

logger = logging.getLogger("scrapers.internship_util")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_KEYWORDS_PATH = _CONFIG_DIR / "internship_keywords.json"

_keywords_cache: dict | None = None
_DEFAULT_KEYWORDS = {"weights": {"實習生": 3}, "regex_weights": {r"\bintern\b": 3}, "threshold": 2}


def _load_keywords() -> dict:
    global _keywords_cache
    if _keywords_cache is not None:
        return _keywords_cache
    try:
        if os.path.exists(_KEYWORDS_PATH):
            with open(_KEYWORDS_PATH, encoding="utf-8") as f:
                _keywords_cache = json.load(f)
        else:
            _keywords_cache = _DEFAULT_KEYWORDS
    except Exception as e:
        logger.warning("讀取設定失敗 %s: %s", _KEYWORDS_PATH, e)
        _keywords_cache = _DEFAULT_KEYWORDS
    return _keywords_cache


def invalidate_cache():
    """外部(main.py)在每次執行前呼叫，確保設定檔變更即時生效。"""
    global _keywords_cache
    _keywords_cache = None
    scholarship_util.invalidate_cache()  # 學校/年級/國籍/身份別排除共用同一份profile


def score_title(text: str, salary: float | None = None) -> int:
    if not text:
        return 0
    data = _load_keywords()
    weights = data.get("weights", {})
    text_lower = text.lower()
    score = sum(w for kw, w in weights.items() if kw.lower() in text_lower)
    for pattern, w in data.get("regex_weights", {}).items():
        if re.search(pattern, text, re.IGNORECASE):
            score += w
    # 高薪加分(2026-07-31使用者確認)：薪資是數字欄位,不是文字關鍵字,
    # 故需另外傳salary參數,不能靠regex_weights比對。
    threshold = data.get("high_salary_threshold")
    if salary is not None and threshold is not None and salary >= threshold:
        score += data.get("high_salary_bonus", 0)
    return score


def get_matched_keywords(text: str) -> list[str]:
    if not text:
        return []
    data = _load_keywords()
    weights = data.get("weights", {})
    text_lower = text.lower()
    matched = [kw for kw in weights if kw.lower() in text_lower]
    matched += [
        pattern for pattern in data.get("regex_weights", {})
        if re.search(pattern, text, re.IGNORECASE)
    ]
    return matched


def _is_excluded_industry(text: str) -> bool:
    """行業別排除(2026-07-31,使用者確認)：餐飲/服務業、業務/銷售類職缺
    即使命中實習關鍵字達門檻,也不是使用者想要的實習類型,整筆排除。"""
    data = _load_keywords()
    excludes = data.get("exclude_keywords", [])
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in excludes)


def _is_excluded_by_profile(text: str) -> bool:
    """學校/年級/國籍/身份別排除(2026-07-31使用者確認套用)——直接重用
    scholarship_util既有規則+同一份config/scholarship_profile.json(同一個
    人的條件,不重複維護一份設定)。這幾個函式本身是純文字/regex比對,不是
    寫死給獎學金專用,套用在實習職缺文本上一樣成立。"""
    profile = scholarship_util.load_profile()
    return (
        scholarship_util.is_excluded_by_school(text, profile)
        or scholarship_util.is_excluded_by_grade(text, profile)
        or scholarship_util.is_excluded_by_nationality(text, profile)
        or scholarship_util.is_excluded_by_special_status(text, profile)
    )


def is_excluded_by_major(text: str, profile: dict | None = None) -> bool:
    """科系排除(2026-07-31使用者確認新增)——scholarship_util沒有這個維度。
    「科系不限/不拘科系」等明確開放句型視為未限制,不排除。啟發式字串比對,
    無法涵蓋所有「OO相關科系」的語意範圍(例如「社會相關科系」不會被規則
    辨識出涵蓋社會學系這個具體系名)——偏保守寧可漏放,不做強排除誤殺,
    這點跟scholarship_util的_is_same_school前綴比對一樣是已知的簡化取捨。"""
    if not text:
        return False
    profile = profile or scholarship_util.load_profile()
    aliases = profile.get("major_aliases") or ([profile["major"]] if profile.get("major") else [])
    if not aliases:
        return False
    if re.search(r"科系不限|不限科系|不拘科系|各科系皆可|科系不拘|不限系所", text):
        return False
    m = re.search(r"限([一-龥]{2,12}(?:相關)?(?:科系|學系|系))(?:畢業|在學)?(?:學生|生|尤佳)?", text)
    if not m:
        return False
    required = m.group(1)
    return not any(alias in required or required in alias for alias in aliases)


def _is_probation_period_noise(text: str, matched: list[str]) -> bool:
    """排除「N個月實習期/實習期間」這種新人試用期慣用句型——這種情況下
    matched通常只會命中最泛用的「實習」二字本身，其餘更明確的複合詞/
    regex都不會命中。只有兩個條件同時成立(命中noise_regex且僅靠「實習」
    二字達標)才排除，避免誤殺「工讀生｜實習」這種真實但用詞分散的職缺。"""
    data = _load_keywords()
    noise_patterns = data.get("noise_regex", [])
    if not noise_patterns:
        return False
    if not any(re.search(p, text) for p in noise_patterns):
        return False
    return matched == ["實習"]


def is_relevant(text: str, min_score: int | None = None, salary: float | None = None) -> bool:
    if not text:
        return False
    data = _load_keywords()
    threshold = min_score if min_score is not None else data.get("threshold", 2)
    if score_title(text, salary) < threshold:
        return False
    if _is_probation_period_noise(text, get_matched_keywords(text)):
        return False
    if _is_excluded_industry(text):
        return False
    if _is_excluded_by_profile(text):
        return False
    if is_excluded_by_major(text):
        return False
    return True
