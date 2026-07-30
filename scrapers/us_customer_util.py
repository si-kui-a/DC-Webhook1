"""
scrapers/us_customer_util.py — 美股客戶新聞稿關鍵字計分工具。

比照internship_util.py的計分模式，只是換一份設定檔(config/us_customer_
keywords.json)。這些公司(Apple/NVIDIA/AMD/Broadcom)的feed絕大多數是一般
企業新聞，用關鍵字篩出真正跟晶片/代工/半導體相關的項目，避免半導體供應鏈
頻道被無關新聞淹沒。
"""
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("scrapers.us_customer_util")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_KEYWORDS_PATH = _CONFIG_DIR / "us_customer_keywords.json"

_keywords_cache: dict | None = None
_DEFAULT_KEYWORDS = {"weights": {"semiconductor": 2, "TSMC": 3}, "threshold": 2}


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


def is_relevant(text: str, min_score: int | None = None) -> bool:
    if not text:
        return False
    data = _load_keywords()
    threshold = min_score if min_score is not None else data.get("threshold", 2)
    return score_title(text) >= threshold
