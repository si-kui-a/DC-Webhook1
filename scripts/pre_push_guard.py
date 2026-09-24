"""
pre_push_guard.py — git push前的強制檢查，安裝於 .git/hooks/pre-push
（見 scripts/install_hooks.py / install_hooks_full.py）。

git對pre-push hook的呼叫慣例：兩個argv參數(remote名稱、remote URL)，
stdin逐行給"<local ref> <local sha1> <remote ref> <remote sha1>"，
每個要push的ref一行；remote sha1全為0代表該ref在遠端還不存在(全新分支)。

2026-09-18新增（同一批「把memory裡先跑XX.sh的反射動作收進commit/push
當下自動執行」，這兩項需要pre-push才能接住，pre-commit接不到）：

- check_force_push_amend_parent_loss()：force-push（非fast-forward）到
  一個遠端已有歷史的ref時，若local sha1是orphan root commit(沒有parent)，
  這是shallow clone(--depth 1)對邊界commit執行`git commit --amend`的
  典型症狀(git會悄悄重建成無parent的root，而不是報錯)，force-push下去
  會截斷遠端分支歷史。邏輯鏡像自C:\\Projects\\_scripts\\
  verify_amend_parent_before_force_push.sh，判斷時機從「push前手動執行」
  改成push當下自動判斷，阻擋push。
- check_new_branch_hints()：push全新分支(remote sha1全為0)時，印出這個
  repo實際的預設分支名稱(不能假設是main——這個使用者的repo有main也有
  master，見feedback_pr_batch_default_branch_and_stacking.md)，並檢查
  這個新分支是否是從另一個尚未合併的本機分支長出來(stacking)，是的話
  提醒開PR時--base要設成那個分支，不要每個都對預設分支開。只提醒不
  阻擋——用哪個base仍需要人工確認實際情況。

以上檢查只在能拿到git資訊時執行；任何一步取不到資料就靜默跳過，不讓
push在異常環境下無端失敗。
"""
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_ZERO_SHA = "0" * 40


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def _has_parent(sha: str) -> bool:
    result = _run(["git", "rev-list", "--parents", "-n", "1", sha])
    parts = result.stdout.strip().split()
    return len(parts) > 1  # 第一個token是commit自己，之後才是parent(s)


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True
    )
    return result.returncode == 0


def _default_branch_name():
    result = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
    ref = result.stdout.strip()
    if ref:
        return ref.rsplit("/", 1)[-1]
    result = _run(["git", "ls-remote", "--symref", "origin", "HEAD"])
    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            return line.split()[1].rsplit("/", 1)[-1]
    return None


def _local_branch_names() -> list:
    result = _run(["git", "branch", "--format=%(refname:short)"])
    return [b.strip() for b in result.stdout.splitlines() if b.strip()]


def check_force_push_amend_parent_loss(pushes: list) -> bool:
    ok = True
    for local_ref, local_sha, remote_ref, remote_sha in pushes:
        if local_sha == _ZERO_SHA or remote_sha == _ZERO_SHA:
            continue  # 刪除ref，或全新ref，都不是這個bug的場景
        if _is_ancestor(remote_sha, local_sha):
            continue  # fast-forward，沒有改寫歷史
        if not _has_parent(local_sha):
            print(
                f"[BLOCKED] {local_ref} 是force-push，但{local_sha[:10]}是orphan root "
                f"commit（沒有parent）——{remote_ref}原本已有歷史，這通常代表shallow "
                f"clone(--depth 1)的amend把邊界commit重建成斷頭的root。修法：git fetch "
                f"--unshallow後重新amend，或用--depth 2以上重clone"
                f"（見feedback_git_shallow_clone_amend_parent_loss.md）。"
            )
            ok = False
    return ok


def check_new_branch_hints(pushes: list) -> bool:
    new_branches = [
        local_ref for local_ref, local_sha, _remote_ref, remote_sha in pushes
        if remote_sha == _ZERO_SHA and local_sha != _ZERO_SHA
    ]
    if not new_branches:
        return True
    default_branch = _default_branch_name()
    other_branches = [b for b in _local_branch_names() if default_branch is None or b != default_branch]
    for local_ref in new_branches:
        # local_ref是"refs/heads/<branch>"，branch本身可能含"/"（如feature/x），
        # 不能用rsplit("/",1)取最後一段——那會把"feature/x"截成"x"。
        branch_name = local_ref[len("refs/heads/"):] if local_ref.startswith("refs/heads/") else local_ref
        if default_branch:
            print(f"[提醒] {branch_name} 是全新分支，這個repo的預設分支是「{default_branch}」，開PR時--base請用這個，不要假設是main。")
        for other in other_branches:
            if other == branch_name:
                continue
            if default_branch and _is_ancestor(other, f"origin/{default_branch}"):
                continue  # already merged into the default branch -- not a stacking risk
            if _is_ancestor(other, branch_name):
                print(
                    f"[提醒] {branch_name} 是從尚未合併的分支「{other}」長出來的(stacking)，"
                    f"開PR時--base請設成「{other}」，不要對「{default_branch or '預設分支'}」開，"
                    f"否則PR會帶著前面分支的重複diff"
                    f"（見feedback_pr_batch_default_branch_and_stacking.md）。"
                )
    return True  # 只提醒，實際base要用哪個仍需要人工判斷，不自動阻擋


def main():
    lines = [line.split() for line in sys.stdin if line.strip()]
    pushes = [tuple(parts) for parts in lines if len(parts) == 4]
    ok = True
    ok &= check_force_push_amend_parent_loss(pushes)
    check_new_branch_hints(pushes)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
