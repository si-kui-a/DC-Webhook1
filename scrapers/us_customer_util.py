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


# PAT-25：weights是純substring比對時，"ASIC"⊂"basic"這種嵌在無關單字
# 內部的誤判可以用詞界修正——這份設定檔的weights鍵全部是英文字/片語
# (跟internship_util.py/scholarship_util.py那種中英混合、中文沒有
# 天然詞界只能substring比對的情況不同)，改用\b詞界比對安全，不會漏抓
# 多字片語("Taiwan Semiconductor"整串頭尾都有詞界)。
#
# 殘留限制(詞界比對修不了，留白供下次B+E式取樣校準判斷)："chip"本身
# 是常見英文人名(如"Chip Bergh")，詞界比對下"Chip"當獨立單字出現時
# 仍會誤中——這不是substring問題，是關鍵字本身語意過廣，需要靠實際
# 樣本判讀決定要不要拿掉或改用更明確的片語，不是regex能解的問題，
# 這裡刻意不單方面移除，比照本檔案_comment已有的「刻意不加裸字」原則。
def _keyword_pattern(kw: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)


def score_title(text: str) -> int:
    if not text:
        return 0
    data = _load_keywords()
    weights = data.get("weights", {})
    score = sum(w for kw, w in weights.items() if _keyword_pattern(kw).search(text))
    for pattern, w in data.get("regex_weights", {}).items():
        if re.search(pattern, text, re.IGNORECASE):
            score += w
    return score


def get_matched_keywords(text: str) -> list[str]:
    if not text:
        return []
    data = _load_keywords()
    weights = data.get("weights", {})
    matched = [kw for kw in weights if _keyword_pattern(kw).search(text)]
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
