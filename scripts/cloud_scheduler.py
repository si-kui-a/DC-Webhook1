"""Hourly dispatcher for the GitHub Actions scheduler (replaces Windows Task Scheduler).

Why one hourly dispatcher instead of one cron per task:
- Actions bills every job rounded up to a whole minute, so 25 separate crons
  cost ~3,000 min/month; one hourly job stays inside the 2,000-min free tier.
- Only one run touches data.db at a time (workflow concurrency group), so the
  SQLite state restored from cache never forks.
- Catch-up: a task whose Taipei-time slot has passed and hasn't succeeded today
  runs on the next hourly tick. This is the StartWhenAvailable behaviour the
  local tasks were missing (battery/sleep silently skipped whole evenings).
- Tasks run one after another, so the 20:00 digests no longer hit Gemini
  simultaneously (the 429 bursts seen locally).

State (last success date + attempts per task) lives in work/cloud_schedule_state.json,
which is persisted together with data.db between runs.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "work" / "cloud_schedule_state.json"
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
    "daily_recap": (["main.py", "--source", "daily_recap"], 7, None),
    "internship": (["main.py", "--internship"], 9, None),
    "twse_chunghwa": (["main.py", "--source", "twse_chunghwa"], 9, None),
    "twse_tsmc": (["main.py", "--source", "twse_tsmc"], 9, None),
    "fed": (["main.py", "--source", "fed"], 9, WEEKDAYS),
    "macro_fred": (["main.py", "--source", "macro_fred"], 9, WEEKDAYS),
    "etf0050": (["main.py", "--source", "etf0050"], 9, WEEKDAYS),
    "tw_stock_portfolio": (["main.py", "--source", "tw_stock_portfolio"], 15, WEEKDAYS),
    "scholarship": (["main.py", "--scholarship"], 20, None),
    "cbc_digest": (["main.py", "--source", "cbc_digest"], 20, None),
    "crypto_digest": (["main.py", "--source", "crypto_digest"], 20, None),
    "geopolitics_digest": (["main.py", "--source", "geopolitics_digest"], 20, None),
    "macro_tech_digest": (["main.py", "--source", "macro_tech_digest"], 20, None),
    "semi_supply_chain_digest": (["main.py", "--source", "semi_supply_chain_digest"], 20, None),
    "tsmc_digest": (["main.py", "--source", "tsmc_digest"], 20, None),
    "us_stock_digest": (["main.py", "--source", "us_stock_digest"], 20, None),
    # rental_search deliberately not scheduled: data.moi.gov.tw has timed out 20+ runs
    # in a row locally and from runners (2026-09-24); re-add once the source is fixed.
    "crypto_meta": (["main.py", "--source", "crypto_meta"], 21, None),
    "tw_stock_meta": (["main.py", "--source", "tw_stock_meta"], 21, None),
    "crypto_nightly_recap": (["main.py", "--source", "crypto_nightly_recap"], 23, None),
    "check_links": (["scripts/check_links.py"], 5, MONDAY),
}
# Runs on every tick (was every 20 min locally; hourly keeps the free-tier budget).
EVERY_TICK = ("check_triggers", ["check_triggers.py"])


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
    print(f"::group::{name}", flush=True)
    start = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=ROOT, timeout=TASK_TIMEOUT_SEC).returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    print("::endgroup::", flush=True)
    ok = code == 0
    print(f"{name}: exit={code} {time.monotonic() - start:.0f}s", flush=True)
    if not ok:
        print(f"::error title={name}::exit={code}", flush=True)
    return ok


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
    unknown = [n for n in names if n not in TASKS and n != EVERY_TICK[0]]
    if unknown:
        print(f"unknown task(s): {unknown}", file=sys.stderr)
        return 2

    print(f"tick {now.isoformat()} due={names}", flush=True)
    failed = []
    if args.only is None or EVERY_TICK[0] in names:
        if not run(*EVERY_TICK, dry_run=args.dry_run):
            failed.append(EVERY_TICK[0])

    today = now.date().isoformat()
    for name in names:
        if name == EVERY_TICK[0]:
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
