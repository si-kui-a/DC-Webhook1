"""
scrapers/internship_util.py — 台灣實習頻道關鍵字計分工具。

比照scholarship_util.py的計分模式(score_title/is_relevant)，但不套用
scholarship_util.py那套學校/年級/國籍/身份別排除規則——使用者確認2026-07-30
「科系與資歷是通用的」，實習頻道不做這類限制性過濾，只需要判斷「這是不是
一則實習職缺」，故只留計分機制，不搬移scholarship_util.py整套exclude邏輯
(那套是設計給「使用者條件不符就排除」的場景，跟這裡的需求不同)。
"""
import json
import logging
import os
import re
from pathlib import Path

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


def score_title(text: str) -> int:
    if not text:
        return 0
    data = _load_keywords()
    weights = data.get("weights", {})
    text_lower = text.lower()
    score = sum(w for kw, w in weights.items() if kw.lower() in text_lower)
    for pattern, w in data.get("regex_weights", {}).items():
        if re.search(pattern, text, re.IGNORECASE):
            score += w
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


def is_relevant(text: str, min_score: int | None = None) -> bool:
    if not text:
        return False
    data = _load_keywords()
    threshold = min_score if min_score is not None else data.get("threshold", 2)
    if score_title(text) < threshold:
        return False
    if _is_probation_period_noise(text, get_matched_keywords(text)):
        return False
    return True
