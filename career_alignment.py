"""
career_alignment.py — 履歷對齊分析（完全零AI）。

設計定案於2026-08-01（見memory project-career-repo-scraper-integration-
plan），2026-08-30實作：讀取career-profile倉庫(si-kui-a/career-profile)
的profile.yaml target_roles清單，對每個target_role重爬104/yes123/gift
(關鍵字改用該角色名稱)+518(2026-08-07因「實習」關鍵字0%命中率被main.py
排程移除，但scraper機制本身沒壞，換成職缺名稱關鍵字重新評估合理，故
career_alignment仍納入)+從MOL既有1000筆快照本地端篩選，統計「這些真實
招募文案裡最常見的詞彙」跟「履歷裡缺少哪些」的落差——不用固定詞彙表、
不呼叫AI，資料驅動。

低樣本規則：某個target_role真實爬到不足MIN_SAMPLE_SIZE筆時，標示
「樣本不足，僅供參考」。獨立手動觸發，不排程，不套用internship_util的
既有排除規則(這是統計分析，不是推播決定，樣本大反而更準)，唯讀報告，
不自動改config/internship_keywords.json。
"""
import logging
import os
import re
import subprocess
from pathlib import Path

import jieba.posseg as pseg

from scrapers import internship_104, internship_518, internship_yes123, internship_gift, internship_mol

logger = logging.getLogger("career_alignment")

CAREER_REPO_DIR = Path(os.getenv("CAREER_REPO_PATH") or r"C:\Projects\10-1912_Career_Profile_職涯履歷檔案庫")
MIN_SAMPLE_SIZE = 10
COVERAGE_THRESHOLD = 0.3  # 「文件覆蓋率」門檻：≥30%招募文案提到才算普遍要求
MIN_TOKEN_LEN = 2
# 詞性標註只保留名詞家族(n開頭：n/nr/ns/nt/nz/ng...)跟英文，濾掉的/我們
# 這類功能詞——2026-08-01設計定案，履歷跟職缺文案用同一套標準才能公平比對。
_KEEP_POS_PREFIXES = ("n", "eng")
# 2026-08-30實測發現：職缺頁面樣板詞(地點/薪資/公司登記形式等)會被詞性
# 標註誤判為「常見需求詞」，但這些跟履歷落差完全無關——不是技能/資格，
# 是每則職缺頁面版型都有的固定欄位標籤。這是實測抓到的真雜訊，不是
# 事先假設，範圍刻意窄(只排除明確的版型/行政用詞，不做語意判斷)。
_BOILERPLATE_TOKENS = {
    "地點", "薪資", "職缺", "面議", "福利", "獎金", "電話", "傳真", "地址",
    "網站", "公司", "有限公司", "股份", "股份有限公司", "上班", "時間",
    "工作地點", "應徵", "聯絡人", "傳送",
}

# 每個target_role都要各自跑一輪104/518/yes123/gift的抓取+重試，多個
# target_roles會需要一點時間，屬於預期內的延遲，不是bug(見設計文件)。
KEYWORD_SOURCES = [internship_104, internship_518, internship_yes123, internship_gift]


def is_available() -> bool:
    return CAREER_REPO_DIR.is_dir() and (CAREER_REPO_DIR / ".git").is_dir()


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

def pull_latest() -> str | None:
    """git pull失敗時回傳錯誤訊息；成功回傳None。呼叫端決定要不要在
    pull失敗時仍沿用本機既有(可能較舊)的target_roles清單。"""
    if not is_available():
        return f"career-profile倉庫未在本機找到（預期路徑：{CAREER_REPO_DIR}）"
    result = subprocess.run(
        ["git", "pull", "origin", "main"], cwd=CAREER_REPO_DIR,
        capture_output=True, text=True, encoding="utf-8",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        return result.stderr.strip() or "git pull失敗，原因不明"
    return None


def load_target_roles() -> list[str]:
    """解析profile.yaml的target_roles欄位，只支援這一個純字串清單欄位，
    不引入pyyaml依賴(比照專案其餘輕量設定檔解析慣例，見DA Trainer
    data_layer.py的config.local.yaml regex解法)。同時支援flow style
    (單行target_roles: [...]或target_roles: [])跟block style(逐行
    - item)兩種YAML寫法，因為使用者最終會怎麼填不確定。"""
    profile_path = CAREER_REPO_DIR / "profile.yaml"
    if not profile_path.is_file():
        return []
    # utf-8-sig而非utf-8：使用者用Windows工具編輯這個檔案時常見會存成帶
    # BOM的UTF-8(2026-08-30實測發現，PowerShell Out-File -Encoding utf8
    # 預設就會加BOM)，BOM殘留在第一行會讓startswith("target_roles:")
    # 比對失敗。utf-8-sig有BOM會去除、沒有也正常讀，兩種情況都對。
    lines = profile_path.read_text(encoding="utf-8-sig").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("target_roles:"):
            continue
        rest = stripped[len("target_roles:"):].strip()
        if rest.startswith("["):
            inner = rest.strip("[]").strip()
            if not inner:
                return []
            return [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
        roles = []
        for next_line in lines[i + 1:]:
            item = next_line.strip()
            if not item.startswith("-"):
                break
            value = item[1:].strip().strip("'\"")
            if value:
                roles.append(value)
        return roles
    return []


def _tokenize(text: str) -> set[str]:
    """jieba.posseg詞性過濾：只留名詞家族/英文，濾掉的/我們這類功能詞，
    短於MIN_TOKEN_LEN的單字丟棄。履歷文字與職缺文案用同一套斷詞，才能
    公平比對token(不是拿原文做substring比對)。"""
    tokens = set()
    for word, flag in pseg.cut(text):
        w = word.strip()
        if len(w) < MIN_TOKEN_LEN:
            continue
        if not flag.startswith(_KEEP_POS_PREFIXES):
            continue
        if re.fullmatch(r"[\d\W_]+", w):
            continue
        if w in _BOILERPLATE_TOKENS:
            continue
        tokens.add(w)
    return tokens


def _crawl_role(role: str, mol_items: list[dict]) -> list[dict]:
    """對單一target_role抓取104/518/yes123/gift(關鍵字換成role)+從MOL
    既有快照本地端篩選(role文字出現在title/_filter_text裡即算命中，
    MOL API本身不支援伺服器端關鍵字搜尋，只能本地端篩，見internship_
    mol.py模組docstring)。單一來源失敗不中斷整體，記錄警告後跳過該
    來源，其餘來源繼續。"""
    items = []
    for mod in KEYWORD_SOURCES:
        try:
            items.extend(mod.fetch(keyword=role))
        except Exception as e:
            logger.warning(f"{mod.SOURCE_NAME} 對「{role}」抓取失敗，跳過: {e}")
    for item in mol_items:
        haystack = f"{item.get('title', '')} {item.get('_filter_text', '')}"
        if role in haystack:
            items.append(item)
    return items


def analyze(resume_text: str, roles: list[str] | None = None) -> dict:
    """輸入履歷純文字(呼叫端負責extract_text)，回傳
    {"roles": [{"role","sample_size","insufficient_sample",
    "commonly_required","gap_words"}, ...], "no_roles": bool}。
    roles為None時從career-profile的profile.yaml讀取；傳入空list以外的
    roles參數主要供測試使用，正常呼叫路徑不需要傳。"""
    roles = roles if roles is not None else load_target_roles()
    if not roles:
        return {"roles": [], "no_roles": True}

    resume_tokens = _tokenize(resume_text)

    try:
        mol_items = internship_mol.fetch()
    except Exception as e:
        logger.warning(f"MOL快照抓取失敗，本輪略過MOL來源: {e}")
        mol_items = []

    results = []
    for role in roles:
        postings = _crawl_role(role, mol_items)
        sample_size = len(postings)
        insufficient = sample_size < MIN_SAMPLE_SIZE

        posting_token_sets = [
            _tokenize(f"{p.get('title', '')} {p.get('summary') or ''}")
            for p in postings
        ]
        commonly_required = []
        if posting_token_sets:
            all_tokens = set().union(*posting_token_sets)
            for token in all_tokens:
                coverage = sum(1 for s in posting_token_sets if token in s) / len(posting_token_sets)
                if coverage >= COVERAGE_THRESHOLD:
                    commonly_required.append(token)

        gap_words = sorted(t for t in commonly_required if t not in resume_tokens)
        results.append({
            "role": role,
            "sample_size": sample_size,
            "insufficient_sample": insufficient,
            "commonly_required": sorted(commonly_required),
            "gap_words": gap_words,
        })
    return {"roles": results, "no_roles": False}


def format_report_text(result: dict) -> str:
    """把analyze()的回傳值格式化成人類可讀文字，CLI/Discord bot共用。"""
    if result.get("no_roles"):
        return (
            "career-profile的target_roles目前是空的，履歷對齊分析在這裡有內容前"
            "不會有意義的輸出——先去career-profile repo填profile.yaml的"
            "target_roles欄位。"
        )
    lines = ["【履歷對齊分析（零AI，統計真實招募文案）】"]
    for r in result["roles"]:
        lines.append(f"\n■ {r['role']}（樣本數：{r['sample_size']}）")
        if r["insufficient_sample"]:
            lines.append(f"  樣本不足{MIN_SAMPLE_SIZE}筆，以下僅供參考，不建議直接採信。")
        if not r["commonly_required"]:
            lines.append("  沒有抓到常見詞彙（樣本過少或抓取全數失敗）。")
            continue
        if r["gap_words"]:
            lines.append(f"  履歷裡沒提到的常見詞：{'、'.join(r['gap_words'][:20])}")
        else:
            lines.append("  履歷已涵蓋主要常見詞彙。")
    return "\n".join(lines)


def extract_text(file_path: str) -> str:
    """依副檔名解析履歷檔案為純文字(pdf/docx/txt/md)。2026-10-04從已刪除的
    resume_matcher.py搬來(該檔與resume_bot.py一起移除)，失敗直接拋例外，
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


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("用法：python career_alignment.py <履歷檔案路徑>", file=sys.stderr)
        sys.exit(1)
    text = extract_text(sys.argv[1])
    err = pull_latest()
    if err:
        print(f"警告：career-profile git pull失敗，沿用本機既有清單：{err}", file=sys.stderr)
    print(format_report_text(analyze(text)))
