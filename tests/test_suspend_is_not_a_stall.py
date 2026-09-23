"""A closed lid is not a stalled engine, and the engine should say so.

`asyncio.sleep` runs on the monotonic clock, which macOS pauses while the
machine is asleep. A 300-second wait therefore lasts 300 seconds of AWAKE
time, however long the lid was shut. From outside, the engine looks frozen:
`last_cycle_at` ages in wall-clock terms and the cycle timeout does not fire
either, because it reads the same paused clock.

This cost two investigations before the cause was found. 2026-09-23:

    15:00:03  last cycle
    15:02:27  Entering Sleep state due to 'Clamshell Sleep' (battery, 70%)
    15:47:25  DarkWake

48 wall-clock minutes containing about 3 minutes of awake time. Nothing was
wrong and nothing in the process could say so. `caffeinate -ims` was running
throughout and does not help — its `-s` inhibits sleep only on AC power.

Filed as a NOTE. A laptop closing its lid is not a fault, and counting it as
one would put the error counter back where the rest of this session's work
took it from.
"""

import asyncio

import pytest

from trading.fund_scheduler import (
    SUSPEND_SLACK_SECONDS,
    FundScheduler,
    SchedulerMetrics,
)


def _scheduler():
    s = object.__new__(FundScheduler)
    s.metrics = SchedulerMetrics()
    s._stop_event = None
    return s


def _sleep_taking(wall_seconds, requested, monkeypatch):
    """Run `_sleep(requested)` while wall-clock appears to advance by
    `wall_seconds` — the shape a suspend produces."""
    sched = _scheduler()
    clock = iter([1000.0, 1000.0 + wall_seconds])
    monkeypatch.setattr("trading.fund_scheduler.time.time", lambda: next(clock))

    async def _instant(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)

    asyncio.run(FundScheduler._sleep(sched, requested))
    return sched


def test_a_suspend_is_reported_as_a_note(monkeypatch):
    sched = _sleep_taking(wall_seconds=2880.0, requested=300.0,
                          monkeypatch=monkeypatch)
    assert sched.metrics.notes == 1
    assert sched.metrics.errors == 0, "a closed lid is not a fault"
    assert "suspended" in sched.metrics.last_note
    assert "43 minutes" in sched.metrics.last_note


def test_the_note_explains_the_wall_clock_gap(monkeypatch):
    """The next person to see an aged `last_cycle_at` should not have to read
    pmset logs to find out nothing was wrong."""
    sched = _sleep_taking(wall_seconds=2880.0, requested=300.0,
                          monkeypatch=monkeypatch)
    assert "wall-clock" in sched.metrics.last_note
    assert "not a stall" in sched.metrics.last_note


def test_an_ordinary_wait_says_nothing(monkeypatch):
    sched = _sleep_taking(wall_seconds=300.4, requested=300.0,
                          monkeypatch=monkeypatch)
    assert sched.metrics.notes == 0
    assert sched.metrics.last_note is None


def test_a_loaded_machine_overshooting_by_seconds_says_nothing(monkeypatch):
    """Only a suspend overshoots by minutes. Reporting every busy loop would
    make the note worthless."""
    sched = _sleep_taking(wall_seconds=300.0 + SUSPEND_SLACK_SECONDS - 1,
                          requested=300.0, monkeypatch=monkeypatch)
    assert sched.metrics.notes == 0


def test_the_threshold_is_minutes_not_seconds():
    assert SUSPEND_SLACK_SECONDS >= 60.0


def test_a_stop_event_path_is_also_measured(monkeypatch):
    """Both branches of the wait, or the note appears only when no stop event
    is wired — which is never, in the real scheduler."""
    sched = _scheduler()
    sched._stop_event = asyncio.Event()
    clock = iter([1000.0, 1000.0 + 2880.0])
    monkeypatch.setattr("trading.fund_scheduler.time.time", lambda: next(clock))

    async def _instant(awaitable, timeout=None):
        awaitable.close()          # the coroutine is never awaited here
        return None
    monkeypatch.setattr(asyncio, "wait_for", _instant)

    asyncio.run(FundScheduler._sleep(sched, 300.0))
    assert sched.metrics.notes == 1
