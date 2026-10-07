"""Offline tests for scripts/cloud_scheduler.py: schedule/catch-up/retry-cap logic.

Real tasks are replaced by tiny python -c commands so nothing is scraped or pushed.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cloud_scheduler as cs  # noqa: E402

OK = ["-c", "pass"]
FAIL = ["-c", "import sys; sys.exit(1)"]


@pytest.fixture
def sched(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cs, "LOG_DIR", tmp_path / "logs")  # task output would land in the real work/logs
    monkeypatch.setattr(cs, "EVERY_TICK", {"check_triggers": OK, "thu_events": OK})
    monkeypatch.setattr(cs, "TASKS", {
        "morning": (OK, 9, None),
        "broken": (FAIL, 9, None),
        "weekday": (OK, 9, cs.WEEKDAYS),
    })

    def tick(now: str) -> int:
        monkeypatch.setattr(sys, "argv", ["cloud_scheduler.py", "--now", now])
        return cs.main()
    return tick


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=cs.TAIWAN_TZ)


def test_not_due_before_slot(sched):
    assert cs.due_tasks(at("2026-09-24T08:05"), {}) == []


def test_weekday_task_skipped_on_saturday(sched):
    assert "weekday" not in cs.due_tasks(at("2026-09-26T09:05"), {})


def test_success_runs_once_per_day_and_failure_is_capped(sched):
    assert sched("2026-09-24T09:05") == 1  # "broken" fails
    state = cs.load_state()
    assert state["morning"]["last_success"] == "2026-09-24"
    assert "last_success" not in state["broken"]

    assert cs.due_tasks(at("2026-09-24T10:05"), state) == ["broken"]  # catch-up retry only
    sched("2026-09-24T10:05")
    sched("2026-09-24T11:05")
    assert cs.load_state()["broken"]["attempts"] == cs.MAX_ATTEMPTS_PER_DAY
    assert cs.due_tasks(at("2026-09-24T12:05"), cs.load_state()) == []  # gave up for today

    # next day everything is due again and the attempt counter resets
    assert set(cs.due_tasks(at("2026-09-25T09:05"), cs.load_state())) == {"morning", "broken", "weekday"}


def test_missed_slot_catches_up_later_same_day(sched):
    assert set(cs.due_tasks(at("2026-09-24T22:05"), {})) == {"morning", "broken", "weekday"}
