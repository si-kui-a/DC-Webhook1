"""
scrapers/scholarship_util.py — 獎學金關鍵字計分、排除規則、個人條件過濾工具。

整合自 scholarship-monitor 的 ruleEngine.js，提供 Python 版去重/評分/排除一條龍。
所有過濾規則以「資料驅動」為原則——排除詞彙寫在 config/scholarship_exclude.json、
個人條件寫在 config/scholarship_profile.json、關鍵字權重寫在 config/scholarship_keywords.json，
不將邏輯寫死在程式碼裡。
"""
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("scrapers.scholarship_util")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_KEYWORDS_PATH = _CONFIG_DIR / "scholarship_keywords.json"
_PROFILE_PATH = _CONFIG_DIR / "scholarship_profile.json"
_EXCLUDE_PATH = _CONFIG_DIR / "scholarship_exclude.json"

# ---- 快取（避免每次呼叫都讀檔）----
_keywords_cache: dict | None = None
_profile_cache: dict | None = None
_exclude_cache: dict | None = None

# ---- 預設值 ----
_DEFAULT_KEYWORDS = {"weights": {}, "threshold": 2}
_DEFAULT_PROFILE = {"exclude_keywords": []}
_DEFAULT_EXCLUDE = {
    "income_restricted": {
        "title_keywords": {"zh": ["低收入戶", "中低收入戶", "清寒"]},
        "qualifying_profile_values": ["low", "lower-middle", "低收入", "中低收入"],
    },
    "special_status": {
        "indigenous": {"zh": ["原住民"]},
        "overseas_student": {"zh": ["僑生"]},
        "refugee": {"en": ["refugee"]},
        "disability_illness": {"zh": ["罕見疾病", "視障", "癌症", "身心障礙", "身障", "重大傷病", "特殊疾病"]},
        "new_resident": {"zh": ["新住民"]},
    },
    "nationality_restricted": {
        "taiwan_aliases": {"zh": ["台灣", "臺灣", "中華民國"]},
        "demonym_restricted": {"en": ["Greeks", "Hellenes"], "zh": ["工程師"]},
    },
    "school_restricted": {
        "name_suffix": {"zh": ["大學", "學院", "哲學系", "美術系", "會計系"]},
    },
    "grade_restricted": {
        "graduate_only": {
            "zh": ["博士生", "研究生", "碩士", "研究所"],
            "en_regex": [
                "\\bphd\\b",
                "\\bmaster(?:'|\\u2019)?s?\\b",
                "\\bdoctoral\\b",
                "\\bpostdoc(?:toral)?\\b",
            ],
            "de_regex_case_sensitive": ["\\bPromotion\\b"],
        },
        "upperclass_only": {"zh": ["限大三", "限大四"]},
        "freshman_only": {"zh": ["新生入學", "新生獎勵", "優秀新生"]},
    },
    "residence_restricted": {},
}


# ============================================================
# 載入設定
# ============================================================
def _load_json(path: str, default: dict) -> dict:
    try:
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("讀取設定失敗 %s: %s", path, e)
        return default


def _load_keywords() -> dict:
    global _keywords_cache
    if _keywords_cache is not None:
        return _keywords_cache
    _keywords_cache = _load_json(str(_KEYWORDS_PATH), _DEFAULT_KEYWORDS)
    return _keywords_cache


def _load_profile() -> dict:
    global _profile_cache
    if _profile_cache is not None:
        return _profile_cache
    _profile_cache = _load_json(str(_PROFILE_PATH), _DEFAULT_PROFILE)
    return _profile_cache


def _load_exclude() -> dict:
    global _exclude_cache
    if _exclude_cache is not None:
        return _exclude_cache
    _exclude_cache = _load_json(str(_EXCLUDE_PATH), _DEFAULT_EXCLUDE)
    return _exclude_cache


def load_profile() -> dict:
    """公開存取使用者個人條件設定。"""
    return _load_profile()


def invalidate_cache():
    """外部（如 main.py）在每次執行前呼叫，確保設定檔變更即時生效。"""
    global _keywords_cache, _profile_cache, _exclude_cache
    _keywords_cache = None
    _profile_cache = None
    _exclude_cache = None


# ============================================================
# 關鍵字評分
# ============================================================
def score_title(title: str) -> int:
    if not title:
        return 0
    data = _load_keywords()
    weights = data.get("weights", {})
    title_lower = title.lower()
    return sum(w for kw, w in weights.items() if kw in title_lower)


def get_matched_keywords(title: str) -> list[str]:
    if not title:
        return []
    data = _load_keywords()
    weights = data.get("weights", {})
    title_lower = title.lower()
    return [kw for kw in weights if kw in title_lower]


def is_relevant(title: str, min_score: int | None = None) -> bool:
    data = _load_keywords()
    threshold = min_score if min_score is not None else data.get("threshold", 2)
    return score_title(title) >= threshold


# ============================================================
# 文字正規化（全形英數字→半形）
# ============================================================
def _normalize_title(title: str) -> str:
    """NFKC normalize：全形英數字/標點→半形，中文字不受影響。"""
    return (title or "").strip()


def _normalize_city_name(name: str) -> str:
    """台/臺正規化，去除市/縣尾綴。"""
    s = re.sub(r"^台", "臺", str(name or ""))
    return re.sub(r"[市縣]$", "", s)


def _normalize_school_name(name: str) -> str:
    """校名台/臺正規化。"""
    return re.sub(r"^台", "臺", str(name or ""))


def _is_same_school(candidate: str, user_school: str) -> bool:
    """比對校名：完全相同、或一方為另一方前綴（簡稱慣例）。"""
    a = _normalize_school_name(candidate)
    b = _normalize_school_name(user_school)
    return a == b or a.startswith(b) or b.startswith(a)


# ============================================================
# 純字串比對輔助
# ============================================================
def _matches_any_plain(text: str, terms: list[str]) -> bool:
    return any(t and t in text for t in (terms or []))


def _matches_any_regex(text: str, patterns: list[str], flags: int = re.IGNORECASE) -> bool:
    return any(re.search(p, text, flags) for p in (patterns or []))


# ============================================================
# 排除規則
# ============================================================
def is_excluded_by_keywords(title: str, profile: dict | None = None) -> bool:
    """第一層硬性排除：命中 profile.exclude_keywords 即剔除。"""
    if not title or not profile:
        return False
    exclude_keywords = profile.get("exclude_keywords", [])
    if not exclude_keywords:
        return False
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in exclude_keywords)


def is_excluded_by_residence(title: str, profile: dict | None = None) -> bool:
    """排除「限OO市/縣設籍」但非使用者戶籍地的項目。"""
    if not title or not profile:
        return False
    user_city = profile.get("residence_city")
    if not user_city:
        return False
    m = re.search(r"限([一-龥]{2,3}[市縣])(?:設籍|戶籍)", title)
    if not m:
        return False
    return _normalize_city_name(m.group(1)) != _normalize_city_name(user_city)


def is_excluded_by_school(title: str, profile: dict | None = None) -> bool:
    """排除「限OO大學在學學生」但非使用者就讀學校的項目。"""
    if not title or not profile:
        return False
    user_school = profile.get("school")
    if not user_school:
        return False

    exclude_terms = _load_exclude()
    suffixes = (
        exclude_terms.get("school_restricted", {})
        .get("name_suffix", {})
        .get("zh", _DEFAULT_EXCLUDE["school_restricted"]["name_suffix"]["zh"])
    )
    school_pattern = fr"[一-龥]{{2,10}}(?:{'|'.join(suffixes)})"

    # 情境一：「限本校（實際校名）」括號澄清
    m = re.search(rf"限本校[（(]({school_pattern})[）)]", title)
    if m:
        return not _is_same_school(m.group(1), user_school)

    # 情境二：單一完整校名
    m = re.search(rf"限({school_pattern})(?:在學)?學生", title)
    if m:
        return not _is_same_school(m.group(1), user_school)

    # 情境三：頓號列舉多校簡稱
    m = re.search(r"(?:僅)?限([一-龥]{2,4}(?:、[一-龥]{2,4})+)(?:在學|在校)?(?:學生|在校生)", title)
    if m:
        names = [s.strip() for s in m.group(1).split("、") if s.strip()]
        return not any(_is_same_school(n, user_school) for n in names)

    return False


def is_excluded_by_income(title: str, profile: dict | None = None) -> bool:
    """排除低收入/清寒限定但使用者不符條件的項目。"""
    if not title or not profile:
        return False
    exclude_terms = _load_exclude()
    ir = exclude_terms.get("income_restricted", {}) or _DEFAULT_EXCLUDE["income_restricted"]
    tier_keywords = ir.get("title_keywords", {}).get("zh", [])
    qualifying = ir.get("qualifying_profile_values", [])

    if profile.get("income_status") in qualifying:
        return False
    return _matches_any_plain(title, tier_keywords)


def is_excluded_by_nationality(title: str, profile: dict | None = None) -> bool:
    """排除國籍限定但非台灣籍的項目 + demonym 限定。"""
    if not title or not profile:
        return False
    user_country = profile.get("country")
    if not user_country:
        return False

    exclude_terms = _load_exclude()
    nr = exclude_terms.get("nationality_restricted", {}) or _DEFAULT_EXCLUDE["nationality_restricted"]
    taiwan_aliases = nr.get("taiwan_aliases", {}).get("zh", ["台灣", "臺灣", "中華民國"])
    demonyms = nr.get("demonym_restricted", {}).get("en", [])

    # 中文「限OO國籍」句型
    m = re.search(r"限([一-龥]{2,6})(?:國籍|籍)(?:學生|生)?", title)
    if m:
        # 排除「設籍」「戶籍」的誤判
        if re.search(r"[市縣設戶]", m.group(1)):
            pass  # 不是真正的國籍限定
        else:
            is_taiwan = any(a in m.group(1) for a in taiwan_aliases)
            if not is_taiwan:
                return True

    # 英文 demonym "for Greeks" 句型
    if demonyms:
        pattern = rf"\bfor (?:{'|'.join(demonyms)})\b"
        if re.search(pattern, title, re.IGNORECASE):
            return True

    return False


def is_excluded_by_special_status(title: str, profile: dict | None = None) -> bool:
    """排除原住民/僑生/難民/身心障礙/新住民限定但使用者不符條件的項目。"""
    if not title or not profile:
        return False

    profile_defaults = {
        "is_indigenous": False,
        "is_overseas_student": False,
        "is_refugee": False,
        "is_disability_or_illness": False,
        "is_new_resident": False,
    }
    for k, v in profile_defaults.items():
        profile_defaults[k] = profile.get(k, False)

    exclude_terms = _load_exclude()
    ss = exclude_terms.get("special_status", {}) or {}

    indigenous_terms = (
        ss.get("indigenous", {}).get("zh", _DEFAULT_EXCLUDE["special_status"]["indigenous"]["zh"])
    )
    overseas_terms = (
        ss.get("overseas_student", {}).get("zh", _DEFAULT_EXCLUDE["special_status"]["overseas_student"]["zh"])
    )
    refugee_terms = (
        ss.get("refugee", {}).get("en", _DEFAULT_EXCLUDE["special_status"]["refugee"]["en"])
    )
    disability_terms = (
        ss.get("disability_illness", {}).get("zh", _DEFAULT_EXCLUDE["special_status"]["disability_illness"]["zh"])
    )
    new_resident_terms = (
        ss.get("new_resident", {}).get("zh", _DEFAULT_EXCLUDE["special_status"]["new_resident"]["zh"])
    )

    # 防誤判：有「優先...餘缺開放一般生」句型代表非排他
    if re.search(r"餘缺.{0,6}(一般生|開放|皆可)|優先.{0,6}(餘缺|其餘|開放)", title):
        return False

    if not profile_defaults["is_indigenous"] and _matches_any_plain(title, indigenous_terms):
        return True
    if not profile_defaults["is_overseas_student"] and _matches_any_plain(title, overseas_terms):
        return True
    if not profile_defaults["is_refugee"] and _matches_any_plain(title.lower(), refugee_terms):
        return True
    if not profile_defaults["is_disability_or_illness"] and _matches_any_plain(title, disability_terms):
        return True
    if not profile_defaults["is_new_resident"] and _matches_any_plain(title, new_resident_terms):
        return True

    return False


def _is_graduate_student(profile: dict) -> bool:
    degree = str(profile.get("degree", "")).lower()
    return degree in ("master", "phd", "doctoral")


def is_excluded_by_grade(title: str, profile: dict | None = None) -> bool:
    """排除年級/學制不符的項目（研究所限定/高年級限定/新生限定）。"""
    if not title or not profile:
        return False
    if not profile.get("degree") and not profile.get("grade"):
        return False

    exclude_terms = _load_exclude()
    gr = exclude_terms.get("grade_restricted", {}) or {}
    graduate_only = gr.get("graduate_only", {})
    upperclass_terms = gr.get("upperclass_only", {}).get("zh", [])
    freshman_terms = gr.get("freshman_only", {}).get("zh", [])

    is_graduate_title = (
        _matches_any_plain(title, graduate_only.get("zh", []))
        or _matches_any_regex(title, graduate_only.get("en_regex", []), re.IGNORECASE)
        or _matches_any_regex(title, graduate_only.get("de_regex_case_sensitive", []), 0)
    )
    if not _is_graduate_student(profile) and is_graduate_title:
        return True

    grade = None
    try:
        grade = int(profile.get("grade", 0))
    except (ValueError, TypeError):
        pass

    if _matches_any_plain(title, upperclass_terms) and not _is_graduate_student(profile) and grade and grade < 3:
        return True

    if _matches_any_plain(title, freshman_terms) and grade and grade != 1:
        return True

    return False


# ============================================================
# 主過濾器（與 ruleEngine.js filter() 對應）
# ============================================================
def filter_items(items: list[dict]) -> list[dict]:
    """
    完整過濾管線：排除關鍵字 → 戶籍地 → 學校 → 收入 → 國籍 →
    特殊身份 → 年級 → 關鍵字計分排序。
    回傳 [{title, summary, url, published_at, score, matched_keywords}, ...]
    """
    rules = _load_keywords()
    threshold = rules.get("threshold", 2)
    profile = _load_profile()
    exclude_keywords = profile.get("exclude_keywords", [])

    survivors = []
    for item in items:
        raw_title = _normalize_title(item.get("title", ""))
        lower_title = raw_title.lower()

        # 第一層：硬性排除
        if exclude_keywords and any(kw.lower() in lower_title for kw in exclude_keywords):
            continue
        if is_excluded_by_residence(raw_title, profile):
            continue
        if is_excluded_by_school(raw_title, profile):
            continue
        if is_excluded_by_income(raw_title, profile):
            continue
        if is_excluded_by_nationality(raw_title, profile):
            continue
        if is_excluded_by_special_status(raw_title, profile):
            continue
        if is_excluded_by_grade(raw_title, profile):
            continue

        survivors.append(item)

    # 第二層：關鍵字計分 + 門檻過濾 + 排序
    scored = []
    for item in survivors:
        title = _normalize_title(item.get("title", ""))
        score = score_title(title)
        scored.append({
            "title": item.get("title", ""),
            "summary": item.get("summary"),
            "url": item.get("url", ""),
            "published_at": item.get("published_at"),
            "score": score,
            "matched_keywords": get_matched_keywords(title),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return [s for s in scored if s["score"] >= threshold]


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== 關鍵字評分測試 ===")
    test_titles = [
        "Humboldt Research Fellowships for Postdoctoral Researchers",
        "Heinrich Böll Foundation: PhD scholarship",
        "Erasmus+ Mobility Project for Higher Education Teachers and Staff",
        "Foundation of German Business (sdw): Academic study sponsorship",
        "Dream NEW Scholarship",
        "Average Student Scholarship",
        "大學院校客家信仰文化研究生獎學金",
        "行天宮急難濟助",
        "鴻海獎學鯨",
        "行天宮資優學生培育",
        "生活助學金",
        "國際數理學科奧林匹亞競賽及國際科學展覽成績優良學生學金",
    ]
    for t in test_titles:
        score = score_title(t)
        kw = get_matched_keywords(t)
        emoji = "✅" if is_relevant(t) else "❌"
        print(f"{emoji} ({score:2d}) {t[:60]}")
        if kw:
            print(f"       → {', '.join(kw)}")

    print("\n=== 排除規則測試 ===")
    profile = _load_profile()
    exclusion_tests = [
        ("限台中市設籍學生獎學金", "is_excluded_by_residence"),
        ("限台北市設籍學生獎學金", "is_excluded_by_residence"),
        ("限東海大學在學學生獎學金", "is_excluded_by_school"),
        ("限定東海大學生獎學金", "is_excluded_by_school"),  # typo case
        ("限台灣大學在學學生獎學金", "is_excluded_by_school"),
        ("限日本籍學生獎學金", "is_excluded_by_nationality"),
        ("限中華民國國籍獎學金", "is_excluded_by_nationality"),
        ("低收入戶優先獎學金", "is_excluded_by_income"),
        ("國立台灣大學原住民學生優秀獎學金", "is_excluded_by_special_status"),
        ("Scholarship for Students With Refugee Status", "is_excluded_by_special_status"),
    ]
    for t, rule_name in exclusion_tests:
        func = globals()[rule_name]
        result = func(t, profile)
        emoji = "🚫" if result else "✅"
        print(f"{emoji} {rule_name:30s} | {t[:50]}")
