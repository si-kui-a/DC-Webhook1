"""
dev_knowledge_audit.py — 「資深懶散工程師複查」的機械化版本。

目的：把「這份文件是不是過時了/自相矛盾」這類問題，從逐字通讀全repo
才能發現，降成看這份報告的候選清單。只做「找候選」，不做「判斷對錯」
——印出來的每一項仍要人工/AI逐一確認才動手改。

移植自 C:\\Projects\\10-2005_WP_Builder_Playbook_WordPress多平台建置
操作手冊\\general\\repo-audit.js 的方法論，依ip這個repo的實際內容重新
設計檢查項（PAT編號取代技巧編號、Python檔案篇幅取代markdown篇幅，
其餘機械掃描邏輯相同）。

執行方式：`python scripts/dev_knowledge_audit.py`（純stdlib，不用裝
套件）。不進CI（quality.yml），比照wordpress-builder-playbook repo
的既有選擇——這類複查適合session內依情境手動觸發，不適合當成每次
push都要跑的硬性CI gate。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {
    ".git", "venv", ".venv", "__pycache__", "_scaffold_backup", "work",
    "logs", ".pytest_cache", "site-packages", "node_modules",
}


def walk_files(*suffixes: str) -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix in suffixes:
            out.append(path)
    return out


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def check_py_line_outliers() -> None:
    print("== 1. Python檔案篇幅（>800行標記，僅供參考，不代表一定要拆）==")
    counts = []
    for path in walk_files(".py"):
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            continue
        counts.append((lines, rel(path)))
    counts.sort(reverse=True)
    for lines, name in counts[:8]:
        flag = " ⚠️" if lines > 800 else ""
        print(f"  {lines}\t{name}{flag}")


def check_stale_keywords() -> None:
    print("\n== 2. 過時關鍵字候選（每個都要人工確認，不代表一定過時）==")
    markers = ["尚未", "還沒動工", "未確認", "待確認", "TODO", "FIXME"]
    hits = 0
    for path in walk_files(".md"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(text.splitlines(), 1):
            for marker in markers:
                if marker in line:
                    hits += 1
                    print(f"  {rel(path)}:{idx}  [{marker}]  {line.strip()[:70]}")
                    break
    if hits == 0:
        print("  （沒有命中）")
    print(f"  共{hits}處候選——逐一確認是否已被同檔案或其他檔案的較新內容取代")


def parse_pat_numbers() -> list[int]:
    kb = ROOT / "Meta_Dev_Knowledge.md"
    if not kb.is_file():
        return []
    text = kb.read_text(encoding="utf-8")
    return [int(m) for m in re.findall(r"^### \[PAT-(\d+)\]", text, re.MULTILINE)]


def check_pat_continuity() -> None:
    print("\n== 3. Meta_Dev_Knowledge.md PAT編號連續性 ==")
    nums = parse_pat_numbers()
    if not nums:
        print("  （找不到Meta_Dev_Knowledge.md或沒有PAT條目，跳過）")
        return
    print(f"  找到{len(nums)}個PAT條目，編號範圍 {min(nums)}~{max(nums)}")
    expected = set(range(min(nums), max(nums) + 1))
    missing = sorted(expected - set(nums))
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if missing:
        print(f"  ⚠️ 跳號：{missing}")
    if dup:
        print(f"  ⚠️ 重複編號：{dup}")
    if not missing and not dup:
        print("  （連續無跳號無重複）")


def check_pat_cross_references() -> None:
    print("\n== 4. 其他檔案引用的PAT編號是否還存在 ==")
    real_nums = set(parse_pat_numbers())
    if not real_nums:
        print("  （Meta_Dev_Knowledge.md無PAT條目，跳過）")
        return
    ref_nums: set[int] = set()
    broken: list[tuple[str, int]] = []
    for path in walk_files(".md", ".py", ".ps1"):
        if path.name == "Meta_Dev_Knowledge.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in re.finditer(r"PAT-(\d+)", text):
            n = int(m.group(1))
            ref_nums.add(n)
            if n not in real_nums:
                broken.append((rel(path), n))
    print(f"  其他檔案引用了{len(ref_nums)}個不重複的PAT編號")
    if broken:
        print("  ⚠️ 引用了Meta_Dev_Knowledge.md目前不存在的編號：")
        for name, n in broken:
            print(f"    {name} 引用 PAT-{n:02d}（可能已被改號/合併/刪除）")
    else:
        print("  （全部引用的編號都找得到，無需更新）")


def check_missing_readme_sections() -> None:
    print("\n== 5. 頂層README.md/CLAUDE.md是否存在 ==")
    for name in ("README.md", "CLAUDE.md", "Meta_Dev_Knowledge.md"):
        path = ROOT / name
        print(f"  {'✅' if path.is_file() else '⚠️ 缺少'} {name}")


def check_unmerged_branches() -> None:
    print("\n== 6. 未merge分支健檢（2026-08-30新增，2026-08-30全repo分支殘留"
          "清理後訂定；純唯讀，只列名單不做任何刪除/合併判斷——要不要處理"
          "每次都要真人/AI實際讀內容才能決定，見同批新增的「全面收斂稽核」"
          "章節）==")
    default_branch = "main"
    try:
        subprocess.run(
            ["git", "fetch", "--all", "--prune"],
            cwd=ROOT, capture_output=True, timeout=30, check=False,
        )
        result = subprocess.run(
            ["git", "branch", "-r", "--no-merged", f"origin/{default_branch}"],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
        names = [
            line.strip() for line in result.stdout.splitlines()
            if line.strip() and "->" not in line
        ]
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  （檢查失敗，可能不在git repo或git不可用：{e}）")
        return
    if not names:
        print("  （無未merge分支）")
        return
    for name in names:
        short = name.removeprefix("origin/")
        count = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{default_branch}..{name}"],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
        print(f"  {short}：領先{count}個commit，未merge")
    print(f"  共{len(names)}個——是否要救回內容或直接刪除，逐一核對實際"
          f"commit內容才能判斷，不能只憑分支名稱/存在天數猜測")


def main() -> int:
    check_py_line_outliers()
    check_stale_keywords()
    check_pat_continuity()
    check_pat_cross_references()
    check_missing_readme_sections()
    check_unmerged_branches()
    print("\n完成。以上都只是候選，不是結論——逐項人工/AI確認後才動手修。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
