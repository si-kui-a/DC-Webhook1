"""One command to check the whole reminder pipeline (docs/operations/提醒推播流水線.md), read-only.

Checks, each with the fix to run when it fails:
  ticks     scheduler runs in the last 24 h: Apps Script dispatches and the longest gap
  sends     send_at runs in the last 7 days: failures (the next tick resends them)
  secret    THU_EVENTS secret older than private/thu_events.json -> add_thu_event.py --sync
  token     days until the Apps Script GitHub token expires (progress/manual_checks_extra.csv)
  upcoming  the on-time sends of the next 7 days, from the local activity list

Usage:
  python scripts/reminder_health.py [--days-ahead 7]
Exit 1 when something needs fixing; every problem line says how.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import precise_send as ps  # noqa: E402
from jobs import thu_events as te  # noqa: E402

REPO = "si-kui-a/DC-Webhook1"
MIN_DISPATCHES_PER_DAY = 18  # hourly Apps Script ticks, allowing a few slow ones
TOKEN_WARN_DAYS = 30
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def gh_json(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8",
                       creationflags=_NO_WINDOW, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout)


def runs(workflow: str, since: datetime) -> list[dict]:
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = gh_json(f"repos/{REPO}/actions/workflows/{workflow}/runs?per_page=100&created=%3E%3D{stamp}")
    return data.get("workflow_runs", [])


def check_ticks(now: datetime) -> tuple[list[str], list[str]]:
    recent = runs("scheduler.yml", now - timedelta(hours=24))
    ticks = sorted(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in recent)
    dispatches = sum(1 for r in recent if r["event"] == "workflow_dispatch")
    gaps = [b - a for a, b in zip(ticks, ticks[1:])] + ([now - ticks[-1]] if ticks else [])
    longest = max(gaps, default=timedelta(hours=24))
    ok = [f"ok   ticks: {len(ticks)} in 24 h ({dispatches} from Apps Script), longest gap {longest}"]
    bad = []
    if dispatches < MIN_DISPATCHES_PER_DAY or longest > timedelta(hours=3):
        bad.append(f"TODO ticks: {dispatches} Apps Script dispatches in 24 h, longest gap {longest}. "
                   "Check the trigger at script.google.com (project 'DC-Webhook1 hourly trigger' -> 觸發條件, "
                   "執行項目 for errors); token expired -> renew (see token check)")
    return ([] if bad else ok), bad


def check_sends(now: datetime) -> tuple[list[str], list[str]]:
    recent = runs("send_at.yml", now - timedelta(days=7))
    failed = [r for r in recent if r["status"] == "completed" and r["conclusion"] != "success"]
    line = f"{len(recent)} on-time sends in 7 days, {len(failed)} failed"
    if not failed:
        return [f"ok   sends: {line}"], []
    names = ", ".join(f"{r['display_title']} ({r['created_at'][:16]})" for r in failed[:5])
    return [], [f"TODO sends: {line}: {names}. The next tick resends them (precise_verify); "
                "if they keep failing, open the run's log in Actions"]


def check_secret() -> tuple[list[str], list[str]]:
    if not te.EVENTS_PATH.exists():
        return ["ok   secret: no local activity list"], []
    secrets = gh_json(f"repos/{REPO}/actions/secrets").get("secrets", [])
    updated = next((s["updated_at"] for s in secrets if s["name"] == "THU_EVENTS"), None)
    local = datetime.fromtimestamp(te.EVENTS_PATH.stat().st_mtime, timezone.utc)
    if updated and datetime.fromisoformat(updated.replace("Z", "+00:00")) >= local - timedelta(minutes=1):
        return [f"ok   secret: THU_EVENTS up to date ({updated[:16]})"], []
    return [], ["TODO secret: THU_EVENTS is older than private/thu_events.json (or missing): "
                "python scripts/add_thu_event.py --sync"]


def check_token(today: date) -> tuple[list[str], list[str]]:
    path = ROOT / "progress" / "manual_checks_extra.csv"
    row = next((r for r in csv.DictReader(path.open(encoding="utf-8")) if r["key"] == "gas_token_renewal"), None)
    m = re.search(r"\d{4}-\d{2}-\d{2}", (row or {}).get("current", ""))
    if not m:
        return [], ["TODO token: no expiry date for gas_token_renewal in progress/manual_checks_extra.csv"]
    left = (date.fromisoformat(m.group(0)) - today).days
    if left > TOKEN_WARN_DAYS:
        return [f"ok   token: expires {m.group(0)} ({left} days)"], []
    return [], [f"TODO token: expires {m.group(0)} ({left} days): {row['question']}"]


def upcoming(now: datetime, days: int) -> list[str]:
    lines = []
    for event in te.load_events() if te.EVENTS_PATH.exists() else []:
        start = datetime.fromisoformat(event["start"]).replace(tzinfo=ps.TAIWAN_TZ)
        for label, offset in te.REMINDERS:
            at = ps.round_down(start - offset)
            if now < at <= now + timedelta(days=days):
                lines.append(f"     {at:%m-%d %H:%M}  {label:>2}  {event['name'][:40]}")
    return ["info upcoming activity reminders:"] + sorted(lines) if lines else ["info upcoming: none in range"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days-ahead", type=int, default=7)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(ps.TAIWAN_TZ)
    ok, bad = [], []
    checks = {"ticks": lambda: check_ticks(now), "sends": lambda: check_sends(now),
              "secret": check_secret, "token": lambda: check_token(now.date())}
    for name, check in checks.items():
        try:
            good, problems = check()
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
            good, problems = [], [f"TODO {name}: could not run ({e})"]
        ok += good
        bad += problems
    print("\n".join(ok + bad + upcoming(now, args.days_ahead)))
    print(f"\n{'OK' if not bad else f'{len(bad)} to fix'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
