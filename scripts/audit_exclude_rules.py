"""
scripts/audit_exclude_rules.py — 排除規則的稽核（唯一入口）。

三套規則（實習 internship_keywords.json、獎學金 scholarship_exclude.json、
東海行事曆 thu_calendar_exclude.json）各有載入器；這支腳本統一檢查它們：
  1. 結構：JSON 可讀、詞為非空字串、同一清單內不重複、被較長詞包含的詞是冗餘。
  2. 驗收案例：config/exclude_guard_cases.json 的 must_exclude／must_keep
     用真實載入器重跑（與 test_internship_exclude_regressions 同一份案例）。
  3. 真實語料回放：資料庫裡已推播的實習職缺標題，若現行排除詞會擋下，
     列出「詞 → 標題」（新增詞前後都跑，看有沒有誤殺本來要的職缺）。
  4. 覆蓋：每個實習排除詞是否出現在某個 must_exclude 案例（只警告，不擋）。

用法:
    python scripts/audit_exclude_rules.py            # 報告；結構／案例有錯 exit 1
    python scripts/audit_exclude_rules.py --corpus   # 另列語料回放明細
滾動更新流程：加案例 → 改設定檔 → 跑本腳本與測試 → publish_mechanical_change.py。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers import internship_util as iu  # noqa: E402
from scrapers import thu_calendar  # noqa: E402

CFG = ROOT / "config"


def _load(name: str) -> dict:
    return json.loads((CFG / name).read_text(encoding="utf-8"))


def _term_lists() -> dict[str, list[str]]:
    """名稱 → 純子字串排除詞清單（regex 類另列，不在此檢查冗餘）。"""
    out: dict[str, list[str]] = {}
    out["internship.exclude_keywords"] = _load("internship_keywords.json").get("exclude_keywords", [])
    thu = _load("thu_calendar_exclude.json")
    for k, v in thu.items():
        if not k.startswith("_") and isinstance(v, list):
            out[f"thu_calendar.{k}"] = v
    sch = _load("scholarship_exclude.json")
    for cat, body in sch.items():
        if cat.startswith("_") or not isinstance(body, dict):
            continue
        for sub, langs in body.items():
            if isinstance(langs, dict):
                for lang, terms in langs.items():
                    if isinstance(terms, list) and "regex" not in lang:
                        out[f"scholarship.{cat}.{sub}.{lang}"] = terms
    return out


def check_structure(lists: dict[str, list[str]]) -> list[str]:
    errs = []
    for name, terms in lists.items():
        bad = [t for t in terms if not isinstance(t, str) or not t.strip()]
        if bad:
            errs.append(f"{name}: 空白或非字串的詞 {bad}")
        strs = [t for t in terms if isinstance(t, str) and t.strip()]
        dup = sorted({t for t in strs if strs.count(t) > 1})
        if dup:
            errs.append(f"{name}: 重複 {dup}")
        low = [t.lower() for t in set(strs)]
        for t in sorted(set(strs)):
            longer = [o for o in low if o != t.lower() and t.lower() in o]
            if longer:
                errs.append(f"{name}: 「{t}」與其他詞有包含關係 {longer[:3]}（較長的詞冗餘，可刪）")
    return errs


def check_cases() -> list[str]:
    cases = json.loads((CFG / "exclude_guard_cases.json").read_text(encoding="utf-8"))
    iu.invalidate_cache()
    thu_calendar.invalidate_cache()
    fns = {"internship": iu.is_relevant_job_search, "thu_calendar": thu_calendar.is_relevant_to_students}
    errs = []
    for area, fn in fns.items():
        for t in cases[area]["must_exclude"]:
            if fn(t):
                errs.append(f"{area} 應排除卻保留: {t}")
        for t in cases[area]["must_keep"]:
            if not fn(t):
                errs.append(f"{area} 應保留卻排除: {t}")
    return errs


def corpus_replay() -> dict[str, list[str]]:
    """已推播（published）的實習職缺中，現行排除詞會擋下的：詞 → 標題。"""
    db = ROOT / "data.db"
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    titles = [r[0] for r in con.execute(
        "select title from item where source_id like 'internship.%' and status='published'")]
    hits: dict[str, list[str]] = {}
    terms = _load("internship_keywords.json").get("exclude_keywords", [])
    for t in titles:
        # 顯示標題是「公司 - 職稱」，正式篩選（_filter_text）只看職稱與描述、
        # 不含公司名；回放也只比對職稱，否則公司名帶到「設計」「餐飲」會多報。
        t = t.split(" - ", 1)[-1]
        for kw in terms:
            if kw.lower() in t.lower():
                hits.setdefault(kw, []).append(t)
    return hits


def uncovered_terms() -> list[str]:
    cases = json.loads((CFG / "exclude_guard_cases.json").read_text(encoding="utf-8"))
    text = " ".join(cases["internship"]["must_exclude"]).lower()
    terms = _load("internship_keywords.json").get("exclude_keywords", [])
    return [t for t in terms if t.lower() not in text]


def main() -> int:
    show = "--corpus" in sys.argv
    errs = check_structure(_term_lists()) + check_cases()
    for e in errs:
        print("ERROR", e)
    hits = corpus_replay()
    n = sum(len(v) for v in hits.values())
    print(f"corpus: 已推播實習職缺中，現行排除詞會擋下 {n} 筆（{len(hits)} 個詞）"
          + ("" if show or not n else "；加 --corpus 看明細"))
    if show:
        for kw, ts in sorted(hits.items()):
            for t in ts[:5]:
                print(f"  {kw} → {t}")
    un = uncovered_terms()
    print(f"coverage: {len(un)}/{len(_load('internship_keywords.json').get('exclude_keywords', []))} 個實習排除詞沒有 must_exclude 案例（警告）")
    print("OK" if not errs else f"FAIL: {len(errs)} 項")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
