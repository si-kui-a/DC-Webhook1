"""
pre_commit_guard.py — git commit前的強制檢查，安裝於 .git/hooks/pre-commit
（見 scripts/install_hooks.py）。

2026-07-31新增(反射動作化，取代單純寫在記憶裡但commit當下沒真的檢查的問題)：
- 禁止直接commit到main/master(全域CLAUDE.md Hard Rule「NEVER commit directly
  to main」，這次在CampusAppMVP違反過一次，靠人工事後發現才修正，改用hook
  技術上真的擋下來，不依賴AI每次都記得檢查)——但全新repo的第一個commit允許
  直接進main/master，這個當下沒有東西可以feature branch分支出來
- .ps1檔案缺UTF-8 BOM會讓PowerShell 5.1對非ASCII內容mis-parse(已踩過的坑，
  見 apply_event_triggered_crypto.ps1 事故)，commit前檢查暫存區裡的.ps1都有BOM
- secrets(.env/簽章金鑰)不該進版控，即使.gitignore有排除也可能因為手動
  git add -f或設定漏配意外進暫存區，commit前再擋一次

2026-09-18新增（把散落在memory裡「先跑XX.sh」型的反射動作，能機械判定的
部分收進commit當下自動執行，不依賴任何人記得去讀memory才會做）：
- drafts/*.md若用反引號點名交付倉庫(草稿慣例寫法，如「`wedding-marketing-
  archive` repo」)，且C:\\Projects下已存在同名(比對git remote slug，不比對
  資料夾顯示名稱)倉庫，commit時提醒核對是否早已實作完成——只提醒不阻擋，
  因為「是否真的已實作完成」需要語意判斷，非機械可定
- 暫存區diff內容比對常見密鑰pattern(AWS/GitHub Token/Discord Webhook/Slack/
  Telegram/Private Key等)，命中就阻擋commit——這是`check_no_secret_files()`
  的延伸(該函式只查檔名，這裡查內容)，也是`C:\\Projects\\_scripts\\
  scan_git_secrets.sh`的輕量鏡像版本(該script掃整個git歷史，太慢不適合
  每次commit都跑；這裡只掃當次暫存的diff，pattern定義兩邊要保持同步，
  這裡改了記得也要檢查那支script要不要一起補pattern，反之亦然)
- 手動mv改名/搬移檔案後用glob模式`git add`，只會抓到新檔名，不會自動把
  舊路徑的刪除記錄進暫存區，導致舊內容留在git歷史裡形成重複；commit前
  解析`git status --porcelain`找出孤兒路徑就阻擋(邏輯鏡像自`C:\\Projects\\
  _scripts\\check_unstaged_deletes.sh`，改用純Python重做而非subprocess
  呼叫bash——實測發現從Python呼叫bash在git hook執行環境下有路徑轉換問題)
- 暫存區刪除檔案數量達到門檻時提醒(不阻擋)先確認過刪除的是什麼再commit
  ——小尺寸/副檔名這類表面線索不足以判斷刪除是否安全，這裡只負責在
  commit當下把人的注意力導向「先看清楚要刪的是什麼」，實際判斷仍需人工

以上4項若C:\\Projects或C:\\Projects\\_scripts在目前這台機器上不存在，
一律靜默跳過(return True)，不讓commit在其他機器/環境上无端失敗。
"""
import re
import subprocess
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_PROJECTS_ROOT = r"C:\Projects"


def check_not_main_branch() -> bool:
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()
    if branch not in ("main", "master"):
        return True
    # 全新repo的第一個commit允許直接進main/master——這個當下沒有任何既有
    # 內容可以拿來開feature branch，規則要防的是「已有內容後還繼續往main
    # 塞」，不是「怎麼有main分支存在」本身。用HEAD存不存在判斷是不是首個commit。
    has_commits = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        capture_output=True, text=True, encoding="utf-8"
    ).returncode == 0
    if not has_commits:
        return True
    print(f"[BLOCKED] 目前在 {branch} 分支，禁止直接commit，請先建立feature branch再commit。")
    return False


def check_ps1_bom() -> bool:
    # core.quotePath=false: git預設會把非ASCII檔名(如中文)octal-escape並包
    # 引號輸出,下面逐行比對會整段比對失敗(2026-08-30在local-kit-source的
    # CI Linux runner上發現，見si-kui-a/local-kit-source#2)。
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, encoding="utf-8"
    )
    ok = True
    for f in result.stdout.strip().splitlines():
        if not f.lower().endswith(".ps1"):
            continue
        try:
            with open(f, "rb") as fh:
                head = fh.read(3)
        except FileNotFoundError:
            continue
        if head != b"\xef\xbb\xbf":
            print(f"[BLOCKED] {f} 是.ps1檔但沒有UTF-8 BOM，PowerShell 5.1對非ASCII內容會mis-parse。")
            ok = False
    return ok


def check_no_secret_files() -> bool:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, encoding="utf-8"
    )
    ok = True
    for f in result.stdout.strip().splitlines():
        basename = os.path.basename(f)
        is_env_secret = basename == ".env" or (
            basename.startswith(".env.") and basename != ".env.example"
        )
        is_key_file = basename in ("key.properties",) or basename.endswith((".jks", ".keystore", ".p12"))
        if is_env_secret or is_key_file:
            print(f"[BLOCKED] {f} 疑似secrets/簽章金鑰檔案，不應該被commit。")
            ok = False
    return ok


# 草稿慣例是用反引號點名交付倉庫，如「`wedding-marketing-archive` repo」
# （2026-09-18對照project-ideas-drafts真實草稿內文驗證過pattern）
_DELIVERY_TARGET_PATTERN = re.compile(r"`([A-Za-z0-9_\-]+)`\s*repo")


def _scan_project_repo_slugs(projects_root: str) -> dict:
    # 專案資料夾名稱是「NN-MXX_English_中文」編號慣例，跟GitHub repo slug
    # (如wedding-marketing-archive)完全不同拼法，比對資料夾名稱字串會漏掉
    # 真正的match——2026-09-18在10-1911_Wedding_Marketing_婚紗行銷檔案這個
    # 實例上驗證過，資料夾名稱裡根本不含"wedding-marketing-archive"這個
    # 子字串。改讀每個子資料夾.git的origin remote URL取得真正的repo slug。
    slugs = {}
    for name in os.listdir(projects_root):
        path = os.path.join(projects_root, name)
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        result = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8"
        )
        url = result.stdout.strip()
        if not url:
            continue
        slug = url.rstrip("/")
        if slug.endswith(".git"):
            slug = slug[:-4]
        slug = slug.rsplit("/", 1)[-1]
        slugs[slug.lower()] = name
    return slugs


def check_draft_delivery_targets() -> bool:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, encoding="utf-8"
    )
    files = [f for f in result.stdout.strip().splitlines() if f.startswith("drafts/") and f.endswith(".md")]
    if not files:
        return True
    if not os.path.isdir(_PROJECTS_ROOT):
        return True
    repo_slugs = _scan_project_repo_slugs(_PROJECTS_ROOT)
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                content = fh.read()
        except FileNotFoundError:
            continue
        for m in _DELIVERY_TARGET_PATTERN.finditer(content):
            target = m.group(1)
            folder = repo_slugs.get(target.lower())
            if folder:
                print(
                    f"[提醒] {f} 點名交付倉庫「{target}」，{_PROJECTS_ROOT}\\{folder} 已存在——"
                    f"commit/merge前請先核對該資料夾是否早已實作完成這份草稿"
                    f"（不阻擋commit，見 feedback_draft_repo_check_target_repo_first.md）。"
                )
    return True  # 只提醒，是否已實作完成需要語意判斷，不自動阻擋


# 輕量鏡像自 C:\Projects\_scripts\scan_git_secrets.sh 的 PATTERNS——那支script
# 掃整個git歷史(git log --all -p)，一次要跑好幾秒到幾十秒，不適合每次commit
# 都執行；這裡只掃當次暫存的diff（新增的行），能在commit當下即時攔截，兩邊
# pattern定義刻意保持一致，改一邊記得檢查另一邊要不要同步補上。
_SECRET_PATTERNS = {
    "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "AWS Secret Key context": re.compile(r"aws_secret_access_key[ \t]*=[ \t]*[A-Za-z0-9/+=]{40}"),
    "GitHub Token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "Discord Webhook": re.compile(r"discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+"),
    "Slack Token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Generic API Key context": re.compile(r"api[_-]?key[ \t]*[:=][ \t]*[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    "Private Key Block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Telegram Bot Token": re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}"),
}


def check_staged_diff_for_secrets() -> bool:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "-U0"],
        capture_output=True, text=True, encoding="utf-8"
    )
    ok = True
    current_file = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        for label, pattern in _SECRET_PATTERNS.items():
            m = pattern.search(added)
            if m:
                value = m.group(0)
                print(
                    f"[BLOCKED] {current_file or '(未知檔案)'} 新增內容疑似含「{label}」："
                    f"{value[:8]}<REDACTED>（長度{len(value)}字元）。"
                )
                ok = False
    return ok


# 邏輯鏡像自 C:\Projects\_scripts\check_unstaged_deletes.sh——原本設計成
# subprocess呼叫該bash script重用同一份實作，但實測發現從Python的
# subprocess呼叫bash在git hook的執行環境下有路徑轉換問題（MSYS bash非
# 互動式被呼叫時，不會像互動式終端機那樣自動轉換Windows路徑，導致
# 「No such file or directory」），改成直接用Python重做這3行邏輯，
# 不依賴bash可執行檔存在或路徑轉換行為。兩邊都是解析`git status
# --porcelain`找「工作區欄位為D」(第2個字元=D)，改一邊記得檢查另一邊
# 要不要同步。2026-09-24修正：原本只比對`" D"`，漏掉`AD`(加進暫存區後
# 又從磁碟刪掉)跟`MD`(改過並暫存後又刪掉)，跟shell版的`^.D `漂移；
# 由scripts/test_guard_parity.py用真實git狀態涵蓋，兩個guard變體一起測。
def check_no_unstaged_deletes() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8"
    )
    orphaned = [
        line[3:] for line in result.stdout.splitlines()
        if len(line) > 3 and line[1] == "D"
    ]
    if orphaned:
        print("[BLOCKED] 以下路徑已經從磁碟消失，但尚未在git暫存區記錄成刪除：")
        for path in orphaned:
            print(f"  - {path}")
        print(
            "      若這是改名/搬移的舊路徑，用 git add -A（或針對單一路徑 "
            "git rm --cached <路徑>）把刪除也記錄進去，再重新commit。"
        )
        return False
    return True


def check_bulk_deletion_needs_review(threshold: int = 3) -> bool:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=D"],
        capture_output=True, text=True, encoding="utf-8"
    )
    deleted = [f for f in result.stdout.strip().splitlines() if f]
    if len(deleted) >= threshold:
        print(
            f"[提醒] 這次commit刪除了{len(deleted)}個檔案，commit前先確認過內容再繼續"
            f"（不阻擋commit，見 feedback_delete_review_rigor.md；"
            f"deleted: {', '.join(deleted[:5])}{'...' if len(deleted) > 5 else ''}）。"
        )
    return True  # 只提醒，刪除是否合理需要人工判斷，不自動阻擋


def main():
    ok = True
    ok &= check_not_main_branch()
    ok &= check_ps1_bom()
    ok &= check_no_secret_files()
    ok &= check_staged_diff_for_secrets()
    ok &= check_no_unstaged_deletes()
    check_draft_delivery_targets()
    check_bulk_deletion_needs_review()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
