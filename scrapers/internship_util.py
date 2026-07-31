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

2026-07-31再修正(移除AI依賴):原本main.py在關鍵字過濾後還會呼叫
ai_insight.classify_internships()做一次Gemini語意消歧,抓「實習」在職缺
文本裡的4種常見假陽性(試用期話術/應徵資格要求/設施名稱/HR管理職敘述)。
該AI呼叫在正式環境撞過一次HTTP 429完全失效,退回用純關鍵字結果推播,
導致這4種假陽性當天全部被推播。改用regex在_is_semantic_noise()裡直接
規則式判斷這4種樣式(patterns從真實推播過的資料反查得出),完全移除這次
AI呼叫，見已刪除的ai_insight.classify_internships()。
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
    """學校/年級/國籍/身份別/戶籍排除(2026-07-31使用者確認套用；戶籍
    排除2026-07-31補上，原本scholarship_util就有is_excluded_by_residence()
    這個函式，只是internship_util沒呼叫到，是遺漏不是設計決定)——直接
    重用scholarship_util既有規則+同一份config/scholarship_profile.json
    (同一個人的條件,不重複維護一份設定)。這幾個函式本身是純文字/regex
    比對,不是寫死給獎學金專用,套用在實習職缺文本上一樣成立。"""
    profile = scholarship_util.load_profile()
    return (
        scholarship_util.is_excluded_by_school(text, profile)
        or scholarship_util.is_excluded_by_grade(text, profile)
        or scholarship_util.is_excluded_by_nationality(text, profile)
        or scholarship_util.is_excluded_by_special_status(text, profile)
        or scholarship_util.is_excluded_by_residence(text, profile)
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


def _is_semantic_noise(text: str, matched: list[str]) -> bool:
    """規則式語意排除(2026-07-31擴大，取代原本的AI語意消歧步驟，見
    ai_insight.py移除的classify_internships())。涵蓋4種「實習」在中文
    職缺文本裡的常見假陽性樣式：(a)新人試用期/教育訓練話術、(b)應徵資格
    要求「具...實習經驗」(要求應徵者已有實習經歷，不是提供實習)、
    (c)實習工場/教室等設施名稱、(d)「規劃/負責...實習專案」這類HR管理職
    敘述(是要應徵者去管理別人的實習，不是本身是實習)——全部regex都是從
    真實推播過的資料反查得出，見config/internship_keywords.json的
    noise_regex。

    只有兩個條件同時成立(命中上述任一noise_regex且matched只有裸字
    「實習」二字)才排除，避免誤殺「工讀生｜實習」「實習生」這種命中
    更明確複合詞的真實職缺——這個保守子集範圍比單純規則式全面替代AI
    小很多，precision因此比2026-07-30測過的純規則版本(約60%)更有把握，
    但仍然是規則式而非語意理解，之後遇到新的真實漏網案例再逐步補規則。"""
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
    if _is_semantic_noise(text, get_matched_keywords(text)):
        return False
    if _is_excluded_industry(text):
        return False
    if _is_excluded_by_profile(text):
        return False
    if is_excluded_by_major(text):
        return False
    return True


def is_relevant_job_search(text: str) -> bool:
    """台灣求職頻道(2026-07-31新增)：跟實習頻道共用同一批來源
    (INTERNSHIP_REGISTRY)+同一份profile/科系/行業排除規則，但反過來——
    只要命中任何實習相關關鍵字/regex(get_matched_keywords非空，含
    「實習」/「實習生」等weights詞、\\bintern\\b等regex)就整筆排除，
    避免同一則職缺同時出現在實習頻道跟這個頻道。不看score是否達
    threshold、不看_is_semantic_noise——這裡的判斷是「有沒有沾到實習
    相關字眼」，不是「這是不是一個夠格的實習職缺」，跟is_relevant()
    的目的不同。也不設任何正向關鍵字門檻，因為這個頻道要含兼職/正職
    等所有非實習類型的職缺。

    使用者確認(2026-07-31)：104/518/yes123/gift這4個來源的原始資料本來
    就是用「實習」關鍵字去對方網站搜出來的結果(見各自SEARCH_KEYWORD)，
    RICH是政府見習/工讀專屬平台，這個函式套用後幾乎全數會被排除——
    這個頻道實質上會以MOL(全國職缺無關鍵字限制的廣泛快照)為主要供稿
    來源，其餘5個來源偶爾漏網的非實習職缺也照樣歡迎，不特別處理，
    不算功能缺陷。"""
    if not text:
        return False
    if get_matched_keywords(text):
        return False
    if _is_excluded_industry(text):
        return False
    if _is_excluded_by_profile(text):
        return False
    if is_excluded_by_major(text):
        return False
    return True


def passes_profile_filters(text: str) -> bool:
    """給像RICH這種本身就是政府見習/工讀專屬平台的來源用(2026-07-31新增)
    ——平台定位本身已經保證是見習/工讀性質的機會，用「實習」關鍵字計分
    門檻反而會把整個來源擋光(官方用語是「見習」不是「實習」)。跳過關鍵字
    計分與語意噪音判斷，但學校/年級/國籍/身份別/科系/行業別排除規則
    仍然套用——這些是使用者對「想要什麼類型職缺」的個人偏好，不因來源
    而不同。"""
    if not text:
        return False
    if _is_excluded_industry(text):
        return False
    if _is_excluded_by_profile(text):
        return False
    if is_excluded_by_major(text):
        return False
    return True
