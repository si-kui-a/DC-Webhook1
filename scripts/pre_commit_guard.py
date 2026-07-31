"""
pre_commit_guard.py — git commit前的強制檢查，安裝於 .git/hooks/pre-commit
（見 scripts/install_hooks.py）。

2026-07-31新增(反射動作化，取代單純寫在記憶裡但commit當下沒真的檢查的問題)：
- 禁止直接commit到main/master(全域CLAUDE.md Hard Rule「NEVER commit directly
  to main」，這次在CampusAppMVP違反過一次，靠人工事後發現才修正，改用hook
  技術上真的擋下來，不依賴AI每次都記得檢查)
- .ps1檔案缺UTF-8 BOM會讓PowerShell 5.1對非ASCII內容mis-parse(已踩過的坑，
  見 apply_event_triggered_crypto.ps1 事故)，commit前檢查暫存區裡的.ps1都有BOM
"""
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def check_not_main_branch() -> bool:
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()
    if branch in ("main", "master"):
        print(f"[BLOCKED] 目前在 {branch} 分支，禁止直接commit，請先建立feature branch再commit。")
        return False
    return True


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


def main():
    ok = True
    ok &= check_not_main_branch()
    ok &= check_ps1_bom()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
