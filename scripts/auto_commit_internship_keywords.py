r"""
scripts/auto_commit_internship_keywords.py — 每月排程，只commit+push
config/internship_keywords.json一個檔案，直接進main分支，不走feature
branch+PR。

這是使用者2026-08-01明確拍板的例外（見memory project-career-repo-
scraper-integration-plan「3. 定期自動commit+push」）：範圍窄(純字串
清單，讀取失敗有_DEFAULT_KEYWORDS兜底，見internship_util.py)、風險低，
**不代表全域規則放寬**——CLAUDE.md「絕不直接commit main」的硬規則對
這個repo其餘檔案、對其他專案依然適用，這個例外僅限這一支排程腳本、
這一個檔案。

只在檔案真的有異動時才commit(git diff --quiet判斷)，沒有異動就直接
結束，不留空commit。

建議排程(Windows Scheduled Task，比照scripts/setup_scheduled_tasks.ps1
既有慣例，每月一次)：
    $Python = 'C:\Projects\10-501_Intel_Pusher_股票情報推播機器人\venv\Scripts\pythonw.exe'
    $Script = 'C:\Projects\10-501_Intel_Pusher_股票情報推播機器人\scripts\auto_commit_internship_keywords.py'
    $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`""
    Register-ScheduledTask -TaskName 'IntelPusher-KeywordsAutoCommit' -Action $action `
        -Trigger (New-ScheduledTaskTrigger -Daily -At 3:00AM `
            -DaysInterval 30) -Force
建立新排程工作需要系統管理員權限，這支腳本本身不會自動排程，只有
被排程觸發時才會執行。
"""
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = "config/internship_keywords.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto_commit_internship_keywords")


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
# 這支腳本規劃要用pythonw.exe(無視窗)排程啟動——git是主控台子系統程式，
# 從無視窗父行程叫出時Windows預設會彈出可見主控台視窗閃現，加這個旗標抑制。
def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        creationflags=_NO_WINDOW,
    )


def main() -> int:
    diff = _run(["git", "diff", "--quiet", "--", TARGET_FILE])
    if diff.returncode == 0:
        logger.info("%s 無異動，不需要commit。", TARGET_FILE)
        return 0

    branch = _run(["git", "branch", "--show-current"]).stdout.strip()
    if branch not in ("main", "master"):
        # 這支腳本設計成只在main/master上跑(見docstring的直接commit例外
        # 範圍)，發現不在預期分支上時中止，避免commit到不相關的WIP分支。
        logger.error("目前在 %s 分支，不是main/master，中止(避免commit到不相關的WIP分支)。", branch)
        return 1

    add = _run(["git", "add", TARGET_FILE])  # 只加這一個檔案，不用-A
    if add.returncode != 0:
        logger.error("git add失敗：%s", add.stderr.strip())
        return 1

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    commit = _run(["git", "commit", "-m", f"chore: auto-sync internship_keywords.json ({ts})"])
    if commit.returncode != 0:
        logger.error("git commit失敗：%s", commit.stderr.strip())
        return 1

    push = _run(["git", "push", "origin", branch])
    if push.returncode != 0:
        logger.error("git push失敗（已commit在本機，需要之後手動push）：%s", push.stderr.strip())
        return 1

    logger.info("已commit+push %s。", TARGET_FILE)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
