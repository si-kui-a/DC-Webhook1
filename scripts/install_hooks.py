"""
install_hooks.py — 安裝 pre-commit + pre-push hook。clone後執行一次即可。

2026-09-18新增pre-push：pre-commit接不到「force-push截斷歷史」「push全新
分支時提醒預設分支/stacking」這兩類檢查(見pre_push_guard.py docstring)，
需要pre-push這個時機點。安裝方式跟pre-commit一致，同一支腳本裝兩個hook。
"""
import os
import stat
import shutil
import subprocess


def find_python_cmd():
    for cmd in ["python3", "python"]:
        if shutil.which(cmd):
            return cmd
    raise RuntimeError("找不到python3或python指令，請確認Python已安裝並加入PATH")


def _write_hook(repo_root, hook_name, script_name, py_cmd):
    hook_path = os.path.join(repo_root, ".git", "hooks", hook_name)
    content = (
        "#!/bin/sh\n"
        '%s "$(git rev-parse --show-toplevel)/scripts/%s" "$@"\n'
        "exit $?\n" % (py_cmd, script_name)
    )
    with open(hook_path, "w", newline="\n") as f:
        f.write(content)
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def install():
    # encoding必須明確指定utf-8:git輸出含中文路徑時,text=True若不指定encoding
    # 會在背景reader thread拋出UnicodeDecodeError(Windows預設走系統codepage如
    # cp950),導致stdout靜默變None(見pre_commit_guard.py同款既有註解——這裡
    # 原本沒套用同樣修法，2026-07-31補上)。
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout.strip()
    py_cmd = find_python_cmd()
    commit_hook = _write_hook(repo_root, "pre-commit", "pre_commit_guard.py", py_cmd)
    push_hook = _write_hook(repo_root, "pre-push", "pre_push_guard.py", py_cmd)
    print("已安裝 pre-commit hook 於 %s（使用 %s）" % (commit_hook, py_cmd))
    print("已安裝 pre-push hook 於 %s（使用 %s）" % (push_hook, py_cmd))


if __name__ == "__main__":
    install()
