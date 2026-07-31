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
"""
import subprocess
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


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
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
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
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
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


def main():
    ok = True
    ok &= check_not_main_branch()
    ok &= check_ps1_bom()
    ok &= check_no_secret_files()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
