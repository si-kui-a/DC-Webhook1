"""
install_hooks.py — 安裝 pre-commit hook。clone後執行一次即可。
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
    hook_path = os.path.join(repo_root, ".git", "hooks", "pre-commit")
    content = (
        "#!/bin/sh\n"
        '%s "$(git rev-parse --show-toplevel)/scripts/pre_commit_guard.py"\n'
        "exit $?\n" % py_cmd
    )
    with open(hook_path, "w", newline="\n") as f:
        f.write(content)
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print("已安裝 pre-commit hook 於 %s（使用 %s）" % (hook_path, py_cmd))


if __name__ == "__main__":
    install()
