"""jobs/precise_send.py — 提醒準時、對齊 5 分鐘送出(2026-10-08新增)。

排程每小時一次、分鐘數不固定(Apps Script)，在排程當下送出就會是 09:13 這種時間。
要準時：排程看到提醒點在 LOOKAHEAD 內，就觸發 .github/workflows/send_at.yml 的一個小
工作，睡到提醒點(對齊 5 分鐘)再送；公開 repo 的 Actions 不計分鐘。

  request()  排程時呼叫：太遠 later、已處理 done、已預約 scheduled、來不及預約或已過 send_now
  run_send_at()  send_at 工作：睡到時間 → 重新產生訊息(RENDERERS) → 用 EDU bot 送出
  run_verify()   每次排程：預約時間過了 GRACE 還沒確認的，查 send_at 的執行結果；失敗或
                 沒跑就補送(訊息標「補送」)，所以送出失敗會被修復，不會靜默漏掉

訊息內容在送出時才重新產生(活動已刪除就不送)，工作參數只有 job、看不出內容的 key 與
時間，因為這個 repo 公開、執行紀錄任何人都看得到。
狀態：work/precise_sends.json(隨加密狀態保存) {"job|key": {"at", "status"}}。
本機執行(沒有 GITHUB_ACTIONS)不預約：時間到了之後的下一次執行直接送。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "work" / "precise_sends.json"
TAIWAN_TZ = timezone(timedelta(hours=8))
WORKFLOW = "send_at.yml"
LOOKAHEAD = timedelta(minutes=75)  # hourly ticks + jitter: every point is seen at least once ahead
MIN_LEAD = timedelta(minutes=3)    # a dispatched job needs about a minute to start
GRACE = timedelta(minutes=15)      # after this, an unconfirmed send is checked
KEEP = timedelta(days=3)


def round_down(dt: datetime) -> datetime:
    """對齊 5 分鐘(12:33 -> 12:30)。提醒寧可早不可晚。"""
    return dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)


def opaque_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state(state: dict, now: datetime) -> None:
    keep = {k: v for k, v in state.items() if datetime.fromisoformat(v["at"]) > now - KEEP}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")


def _api(method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(method, f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
                            headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                                     "Accept": "application/vnd.github+json"}, timeout=20, **kwargs)


def dispatch(job: str, key: str, at: datetime) -> bool:
    if not (os.environ.get("GITHUB_ACTIONS") and os.environ.get("GH_TOKEN")):
        return False
    try:
        r = _api("POST", f"actions/workflows/{WORKFLOW}/dispatches",
                 json={"ref": "main", "inputs": {"job": job, "key": key, "at": at.isoformat()}})
    except requests.RequestException as e:
        logger.warning("[precise_send] dispatch failed: %s", e)
        return False
    if r.status_code != 204:
        logger.warning("[precise_send] dispatch HTTP %s", r.status_code)
    return r.status_code == 204


def request(state: dict, job: str, key: str, at: datetime, now: datetime) -> str:
    """later / done / scheduled / send_now。send_now 時由呼叫端送出，成功後呼叫 mark_sent。"""
    sid = f"{job}|{key}"
    if sid in state:
        return "done"
    if at - now > LOOKAHEAD:
        return "later"
    if at - now > MIN_LEAD:
        if dispatch(job, key, at):
            state[sid] = {"at": at.isoformat(), "status": "scheduled"}
            return "scheduled"
        if at - now > LOOKAHEAD / 3:
            return "later"  # dispatch failed: the next tick tries again while there is time
    return "send_now"


def mark_sent(state: dict, job: str, key: str, at: datetime) -> None:
    state[f"{job}|{key}"] = {"at": at.isoformat(), "status": "sent_now"}


def renderer(job: str):
    """job -> render(key, now) -> 訊息或 None(不必送了)。延後 import 避免循環。"""
    if job == "thu_events":
        from jobs.thu_events import render
    elif job == "thu_lixue":
        from jobs.thu_lixue import render
    elif job == "thu_calendar":
        from jobs.thu_calendar import render
    else:
        raise ValueError(f"unknown job {job}")
    return render


def send(text: str) -> bool:
    import notify_telegram
    return notify_telegram.send_message(text, parse_mode="HTML",
                                        bot_token=notify_telegram.TELEGRAM_EDU_BOT_TOKEN,
                                        chat_id=notify_telegram.TELEGRAM_EDU_CHAT_ID)


def run_send_at(job: str, key: str, at_iso: str) -> bool:
    """send_at.yml 的本體：睡到 at，重新產生訊息後送出。送出失敗回 False(工作失敗，verify 會補送)。"""
    at = datetime.fromisoformat(at_iso)
    wait = (at - datetime.now(TAIWAN_TZ)).total_seconds()
    if wait > 0:
        time.sleep(min(wait, LOOKAHEAD.total_seconds() + 600))
    text = renderer(job)(key, datetime.now(TAIWAN_TZ))
    if text is None:
        logger.info("[precise_send] %s: nothing to send any more", job)
        return True
    return send(text)


def run_verify(now: datetime | None = None) -> bool:
    """每次排程：確認預約的送出成功；失敗或沒跑就補送。"""
    now = now or datetime.now(TAIWAN_TZ)
    state = load_state()
    pending = {sid: v for sid, v in state.items()
               if v["status"] == "scheduled" and datetime.fromisoformat(v["at"]) + GRACE < now}
    if pending and os.environ.get("GH_TOKEN"):
        since = min(datetime.fromisoformat(v["at"]) for v in pending.values()) - LOOKAHEAD - timedelta(hours=1)
        r = _api("GET", f"actions/workflows/{WORKFLOW}/runs",
                 params={"per_page": 100, "created": f">={since.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"})
        runs = r.json().get("workflow_runs", []) if r.ok else None
        if runs is None:
            logger.warning("[precise_send] cannot list send_at runs (HTTP %s); verify next tick", r.status_code)
            return True
        result = {run["display_title"]: run for run in runs}
        for sid, entry in pending.items():
            job, key = sid.split("|", 1)
            run = result.get(f"send {job} {key}")
            if run and run["status"] != "completed" and datetime.fromisoformat(entry["at"]) + timedelta(hours=1) > now:
                continue  # still running (queued late); look again next tick
            if run and run["conclusion"] == "success":
                entry["status"] = "ok"
                continue
            text = renderer(job)(key, now)
            if text is None or send("（補送：預約送出沒有成功）\n" + text):
                entry["status"] = "resent"
                logger.warning("[precise_send] resent %s (run %s)", job, run and run.get("conclusion"))
            else:
                save_state(state, now)
                return False
    save_state(state, now)
    return True
