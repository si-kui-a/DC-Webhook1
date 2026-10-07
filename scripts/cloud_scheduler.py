"""Hourly dispatcher for the GitHub Actions scheduler (replaces Windows Task Scheduler).

Why one hourly dispatcher instead of one cron per task:
- The repo went public on 2026-09-26 (unlimited Actions minutes; the private 2,000-min
  quota is shared with every other private repo and an hourly tick alone was estimated
  at 1,200-2,000 min/month). One job per tick still keeps the logs in one place.
- Only one run touches data.db at a time (workflow concurrency group), so the
  SQLite state restored from cache never forks.
- Catch-up: a task whose Taipei-time slot has passed and hasn't succeeded today
  runs on the next hourly tick. This is the StartWhenAvailable behaviour the
  local tasks were missing (battery/sleep silently skipped whole evenings).
- Tasks run one after another, so the 20:00 digests no longer hit Gemini
  simultaneously (the 429 bursts seen locally).

State (last success date + attempts per task) lives in work/cloud_schedule_state.json,
which is persisted together with data.db between runs.

Public logs: a task's own output goes to work/logs/<task>-<date>.log (kept 7 days, stored
inside the encrypted state bundle), never to the Actions log, which anyone can read on a
public repo. The Actions log only gets name, exit code and duration.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "work" / "cloud_schedule_state.json"
LOG_DIR = ROOT / "work" / "logs"
LOG_KEEP_DAYS = 7
TAIWAN_TZ = timezone(timedelta(hours=8))
WEEKDAYS = frozenset(range(5))  # Mon-Fri, matches the local tasks' DaysOfWeek=62
MONDAY = frozenset({0})
MAX_ATTEMPTS_PER_DAY = 3  # stop retrying a broken source instead of burning minutes all day
TASK_TIMEOUT_SEC = 900  # same ceiling as ops/run_task.ps1

# name -> (argv after python, Taipei hour it becomes due, weekdays or None for daily)
# Hours mirror the local Windows tasks; 20:30 tasks run at 21 (after the 20:00 digests),
# TwStockPortfolio 14:40 -> 15 since the dispatcher ticks once an hour.
TASKS = {
    "thu_calendar": (["main.py", "--source", "thu_calendar"], 6, None),
    "thu_lixue": (["main.py", "--source", "thu_lixue"], 6, None),
    "daily_recap": (["main.py", "--source", "daily_recap"], 7, None),
    "internship": (["main.py", "--internship"], 9, None),
    "twse_chunghwa": (["main.py", "--source", "twse_chunghwa"], 9, None),
    "twse_tsmc": (["main.py", "--source", "twse_tsmc"], 9, None),
    "fed": (["main.py", "--source", "fed"], 9, WEEKDAYS),
    "macro_fred": (["main.py", "--source", "macro_fred"], 9, WEEKDAYS),
    "etf0050": (["main.py", "--source", "etf0050"], 9, WEEKDAYS),
    "tw_stock_portfolio": (["main.py", "--source", "tw_stock_portfolio"], 15, WEEKDAYS),
    "scholarship": (["main.py", "--scholarship"], 20, None),
    # Three digests since 2026-10-04 (were seven, plus two AI-on-AI meta summaries).
    # Substack digests sit in different hours: from runner IPs Substack answers with a
    # Cloudflare challenge and they go through rss2json, which 429s after ~10 keyless
    # calls in a row (2026-09-24). crypto 4 feeds at 19, us_macro 9 feeds at 20 -> under
    # 10 per hourly tick. If only the fallback cron fires, one tick may run both (13
    # calls); set RSS2JSON_API_KEY if that starts failing.
    "crypto_digest": (["main.py", "--source", "crypto_digest"], 19, None),
    "us_macro_digest": (["main.py", "--source", "us_macro_digest"], 20, None),
    "tw_semi_digest": (["main.py", "--source", "tw_semi_digest"], 20, None),
    # Was 23: catch-up only covers the same Taipei day and the last fallback tick is
    # ~21:43, so a 23:00 task ran once in 8 days (2026-10-04). It is a rule-only
    # "no change today" notice, so an earlier slot loses nothing.
    "crypto_nightly_recap": (["main.py", "--source", "crypto_nightly_recap"], 21, None),
    "check_links": (["scripts/check_links.py"], 5, MONDAY),
}
# Run on every tick (was every 20 min locally; hourly keeps the free-tier budget).
# thu_events: reminders hours before a registered campus activity need an hourly look.
EVERY_TICK = {
    "check_triggers": ["check_triggers.py"],
    "thu_events": ["main.py", "--source", "thu_events"],
}


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def due_tasks(now: datetime, state: dict) -> list[str]:
    today = now.date().isoformat()
    due = []
    for name, (_, hour, days) in TASKS.items():
        if days is not None and now.weekday() not in days:
            continue
        if now.hour < hour:
            continue
        entry = state.get(name, {})
        if entry.get("last_success") == today:
            continue
        attempts = entry.get("attempts", 0) if entry.get("attempt_date") == today else 0
        if attempts >= MAX_ATTEMPTS_PER_DAY:
            continue
        due.append(name)
    return due


def run(name: str, argv: list[str], dry_run: bool) -> bool:
    cmd = [sys.executable, *argv]
    if dry_run:
        print(f"[dry-run] {name}: {' '.join(argv)}", flush=True)
        return True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}-{datetime.now(TAIWAN_TZ).date().isoformat()}.log"
    start = time.monotonic()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"--- {datetime.now(TAIWAN_TZ).isoformat()} {' '.join(argv)}\n")
        log.flush()
        try:
            code = subprocess.run(cmd, cwd=ROOT, timeout=TASK_TIMEOUT_SEC,
                                  stdout=log, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    ok = code == 0
    print(f"{name}: exit={code} {time.monotonic() - start:.0f}s", flush=True)
    if not ok:
        print(f"::error title={name}::exit={code} (details in the encrypted state: {log_path.name})", flush=True)
    return ok


def prune_logs(now: datetime) -> None:
    cutoff = (now - timedelta(days=LOG_KEEP_DAYS)).date().isoformat()
    for path in LOG_DIR.glob("*.log"):
        if path.stem[-10:] < cutoff:  # "<task>-YYYY-MM-DD"
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="run exactly these tasks, ignoring the schedule")
    parser.add_argument("--dry-run", action="store_true", help="print what would run, change nothing")
    parser.add_argument("--now", help="override current time (ISO, Taipei) for testing")
    parser.add_argument("--mark-done-today", action="store_true",
                        help="cutover helper: record today's already-due tasks as done without running "
                             "them, so the first cloud tick doesn't resend what the local tasks sent")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=TAIWAN_TZ) if args.now else datetime.now(TAIWAN_TZ)
    state = load_state()
    if args.mark_done_today:
        today = now.date().isoformat()
        marked = due_tasks(now, state)
        for name in marked:
            state[name] = {"last_success": today, "attempt_date": today, "attempts": 0}
        save_state(state)
        print(f"marked done for {today}: {marked}")
        return 0
    names = args.only if args.only is not None else due_tasks(now, state)
    unknown = [n for n in names if n not in TASKS and n not in EVERY_TICK]
    if unknown:
        print(f"unknown task(s): {unknown}", file=sys.stderr)
        return 2

    print(f"tick {now.isoformat()} due={names}", flush=True)
    if not args.dry_run:
        prune_logs(now)
    failed = []
    for name, argv in EVERY_TICK.items():
        if (args.only is None or name in names) and not run(name, argv, dry_run=args.dry_run):
            failed.append(name)

    today = now.date().isoformat()
    for name in names:
        if name in EVERY_TICK:
            continue
        ok = run(name, TASKS[name][0], args.dry_run)
        if args.dry_run:
            continue
        entry = state.setdefault(name, {})
        if entry.get("attempt_date") != today:
            entry["attempt_date"], entry["attempts"] = today, 0
        entry["attempts"] += 1
        if ok:
            entry["last_success"] = today
        else:
            failed.append(name)
        save_state(state)  # after each task, so a later crash can't lose earlier progress

    if failed:
        print(f"failed: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
