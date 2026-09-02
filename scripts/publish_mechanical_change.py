r"""
scripts/publish_mechanical_change.py — 把「機械性/低風險清單增減」類變更
(CLAUDE.md 2026-08-07訂定、範圍限定ip與study-companion的PR自動merge例外)
從「驗證->branch->commit->push->PR->merge->同步main」這一串手動步驟收斂成
一個指令，供以後同類任務重用(2026-09-02新增，起因：同一個session內連續
兩次手動重複這串≥8步git/gh指令去改config/internship_keywords.json，屬於
懶散資深工程師思維該收斂掉的重複勞動)。

只做「驗證+PR自動流程」，不做業務邏輯判斷——呼叫端(人或AI)自己先確認
change屬於這個例外的適用範圍(不含新功能/架構調整/schema變更)，這支
腳本不做語意判斷。

用法:
    python scripts/publish_mechanical_change.py \
        --files config/internship_keywords.json test/test_foo.py \
        --branch chore/some-change \
        --commit-title "chore(internship): 排除XX類" \
        --commit-body "說明..." \
        --pr-title "chore(internship): 排除XX類" \
        --pr-body "PR說明..." \
        --test test/test_internship_keyword_commands.py \
        --test test/test_internship_exclude_regressions.py

行為：
1. 對每個--files路徑，若副檔名是.json，先做json.load()驗證語法。
2. 對每個--test模組(unittest discover相容的module path)跑
   `python -m unittest <module>`，任何一個失敗就中止，不進入git流程
   (驗證優先於發布，見CLAUDE.md「驗證優先」原則)。
3. 若目前在main/master：建立--branch分支；若已經在其他分支：沿用當前
   分支，不重複建立(避免巢狀分支)。
4. git add(只加--files指定的路徑，不用-A，比照auto_commit_internship_
   keywords.py既有慣例)、commit、push。
5. 用gh pr create開PR、gh pr merge --merge --delete-branch自動合併
   (這一步就是套用CLAUDE.md的PR自動merge例外，不再逐次詢問)。
6. merge成功後自動切回main/master並pull同步本機。

任何一步失敗就印出錯誤訊息並以非0結束碼中止，不繼續往下執行——中止時
git/gh的狀態(已建分支/已commit/已push/已開PR)如實保留，不自動回滾，
由呼叫端依錯誤訊息決定下一步(比照CLAUDE.md「不確定時不用破壞性動作」
原則，這支腳本不做任何reset/checkout --丟棄變更的操作)。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(args)}")
    result = subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        creationflags=_NO_WINDOW,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if check and result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(1)
    return result


def validate_json_files(files: list[str]) -> None:
    for f in files:
        if not f.endswith(".json"):
            continue
        path = REPO_ROOT / f
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"JSON驗證失敗: {f}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"JSON語法OK: {f}")


def run_tests(test_modules: list[str]) -> None:
    for module in test_modules:
        mod_name = module.replace("/", ".").replace("\\", ".").removesuffix(".py")
        result = _run([sys.executable, "-m", "unittest", mod_name], check=False)
        if result.returncode != 0:
            print(f"測試失敗，中止發布: {module}", file=sys.stderr)
            sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="+", required=True, help="要commit的檔案路徑(相對repo root)")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit-title", required=True)
    parser.add_argument("--commit-body", default="")
    parser.add_argument("--pr-title", required=True)
    parser.add_argument("--pr-body", required=True)
    parser.add_argument("--test", action="append", default=[], dest="tests", help="可重複，unittest module path")
    parser.add_argument("--repo", default="si-kui-a/DC-Webhook1", help="gh --repo參數")
    args = parser.parse_args()

    validate_json_files(args.files)
    run_tests(args.tests)

    branch = _run(["git", "branch", "--show-current"]).stdout.strip()
    if branch in ("main", "master"):
        _run(["git", "checkout", "-b", args.branch])
    else:
        print(f"已在非main分支 {branch}，沿用該分支，不另建立。")
        args.branch = branch

    _run(["git", "add", *args.files])
    commit_msg = args.commit_title
    if args.commit_body:
        commit_msg += "\n\n" + args.commit_body
    commit_msg += "\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
    _run(["git", "commit", "-m", commit_msg])
    _run(["git", "push", "-u", "origin", args.branch])

    pr_body = args.pr_body + "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    create = _run(["gh", "pr", "create", "--repo", args.repo, "--title", args.pr_title, "--body", pr_body])
    pr_url = create.stdout.strip().splitlines()[-1] if create.stdout.strip() else ""
    print(f"PR: {pr_url}")

    _run(["gh", "pr", "merge", "--repo", args.repo, args.branch, "--merge", "--delete-branch"])

    base_branch = "main" if branch in ("main", "master", "") else branch
    _run(["git", "fetch", "origin", "--prune"])
    _run(["git", "checkout", "main"])
    _run(["git", "pull"])
    print("完成：已merge並同步本機main。")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
