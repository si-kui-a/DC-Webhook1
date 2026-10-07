"""Add a Tunghai campus-activity registration email to the reminder list (jobs/thu_events.py).

Parses the 「東海大學-校園活動報名系統」 confirmation email, keeps only the activity details
(no name, phone or e-mail), saves private/thu_events.json (git-ignored: the repo is public)
and, unless --no-sync, uploads the list to the GitHub secret THU_EVENTS that the cloud
scheduler reads. Activities that ended more than 7 days ago are dropped on every run.

Usage:
  python scripts/add_thu_event.py EMAIL.txt [EMAIL.txt ...] [--no-sync]
  python scripts/add_thu_event.py --list
  python scripts/add_thu_event.py --remove ID [--no-sync]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.thu_events import EVENTS_PATH, TAIWAN_TZ, parse_registration  # noqa: E402

REPO = "si-kui-a/DC-Webhook1"
KEEP_AFTER_END = timedelta(days=7)
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def load() -> list[dict]:
    return json.loads(EVENTS_PATH.read_text(encoding="utf-8")) if EVENTS_PATH.exists() else []


def save(events: list[dict], now: datetime) -> list[dict]:
    keep = [e for e in events
            if datetime.fromisoformat(e["end"]).replace(tzinfo=TAIWAN_TZ) + KEEP_AFTER_END > now]
    keep.sort(key=lambda e: e["start"])
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return keep


def sync() -> bool:
    """Secret value goes through stdin, never onto the command line or the screen."""
    r = subprocess.run(["gh", "secret", "set", "THU_EVENTS", "--repo", REPO],
                       input=EVENTS_PATH.read_text(encoding="utf-8"), text=True, encoding="utf-8",
                       capture_output=True, creationflags=_NO_WINDOW)
    print("ok   synced THU_EVENTS" if r.returncode == 0 else f"FAIL sync: {r.stderr.strip()}")
    return r.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("emails", nargs="*", type=Path)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove", metavar="ID")
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args(argv)
    now = datetime.now(TAIWAN_TZ)
    events = load()
    if args.list:
        for e in events:
            print(f"{e['id']}  {e['start']}  {e['name']}  @ {e['place']}")
        return 0
    if not args.emails and not args.remove:
        ap.error("give EMAIL files, --list or --remove")
    by_id = {e["id"]: e for e in events}
    for path in args.emails:
        try:
            event = parse_registration(path.read_text(encoding="utf-8"))
        except ValueError as err:
            print(f"FAIL {path.name}: {err}")
            return 1
        print(f"{'updated' if event['id'] in by_id else 'added  '} {event['id']}  {event['start']}  {event['name']}")
        by_id[event["id"]] = event
    if args.remove:
        if by_id.pop(args.remove, None) is None:
            print(f"FAIL no activity with id {args.remove}")
            return 1
        print(f"removed {args.remove}")
    kept = save(list(by_id.values()), now)
    print(f"ok   {len(kept)} activities in private/{EVENTS_PATH.name}")
    return 0 if args.no_sync or sync() else 1


if __name__ == "__main__":
    sys.exit(main())
