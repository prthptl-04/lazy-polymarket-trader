"""The scheduler must not report `running` after its loop has died.

What happened, measured: the Robinhood MCP client raised a
`BaseExceptionGroup` at 04:09. `FundScheduler._run` catches `Exception`, and
`BaseExceptionGroup` derives from `BaseException` — so it was not caught, the
task died, and `self.state` stayed `"running"` for the next eleven and a half
hours. The dashboard showed a healthy fund. Nothing cycled, no stop was
enforced, and no error was surfaced.

Two independent defects, fixed separately because either alone would have hidden
the other:

1. The loop did not survive a BaseException it could have survived.
2. `status()` read a stored string instead of the task it describes, so it could
   not tell a running fund from a dead one.

The second is the more important. A loop that dies is a bug; a status that lies
about it is how a bug goes unnoticed through an entire trading session.
"""

import asyncio

import pytest

from trading.fund_scheduler import FundScheduler


class _Fund:
    def __init__(self, boom=None):
        self.boom, self.calls = boom, 0
        self.router = self.kill_switch = None
    async def resume_unfinished(self): return []
    async def run_cycle(self, *a, **kw):
        self.calls += 1
        if self.boom:
            raise self.boom
        return _Report()


class _Report:
    submitted: list = []
    errors: list = []
    halted_reason = None
    def summary(self): return {}


class _Venue:
    async def account(self):
        class _A: equity_usd = 500.0; buying_power_usd = 500.0
        return _A()
    async def positions(self): return []


def _sched(fund, **kw):
    return FundScheduler(fund=fund, venue=_Venue(), cycle_interval_seconds=0.01, **kw)


# ---------------------------------------------------------------- survival

def test_the_loop_survives_the_exception_group_that_killed_it():
    """The exact failure. `BaseExceptionGroup` is not an `Exception`."""
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [KeyboardInterrupt()])

    async def go():
        fund = _Fund(boom=group)
        s = _sched(fund)
        await s.start()
        await asyncio.sleep(0.08)
        await s.stop()
        return fund.calls, s

    calls, s = asyncio.run(go())
    assert calls > 1, "the loop stopped after the first BaseExceptionGroup"
    assert s.metrics.errors > 0, "it survived but did not record the failure"


def test_a_cancel_still_stops_the_loop():
    """CancelledError must propagate, or STOP cannot stop anything."""
    async def go():
        s = _sched(_Fund())
        await s.start()
        await asyncio.sleep(0.05)
        await s.stop()
        return s.state

    assert asyncio.run(go()) == "stopped"


@pytest.mark.parametrize("exc, swallowed", [
    (BaseExceptionGroup("g", [ValueError()]), True),   # the MCP failure
    (RuntimeError("provider down"), True),
    (ValueError("bad row"), True),
    (KeyboardInterrupt(), False),                      # operator intent
    (SystemExit(), False),
    (asyncio.CancelledError(), False),                 # how STOP stops
])
def test_the_loop_absorbs_failures_but_never_operator_intent(exc, swallowed):
    """The policy, tested directly.

    Exercised as a pure predicate rather than through the loop: asyncio
    propagates SystemExit and KeyboardInterrupt from a task to the event loop
    itself, so driving them through `_run` tests asyncio's semantics rather
    than this scheduler's, and a real KeyboardInterrupt hijacks the test runner.
    """
    from trading.fund_scheduler import is_operator_intent
    assert is_operator_intent(exc) is not swallowed


# ---------------------------------------------------------------- honesty

def test_status_reports_dead_when_the_task_is_dead():
    """The defect that cost eleven hours. `status()` must describe the task,
    not a string set when it was created.

    Killed with a real exception, not a cancellation — cancellation is how STOP
    stops and is covered separately. Using cancel() here would have passed
    against a scheduler that called every ordinary stop a death.
    """
    async def go():
        s = _sched(_Fund())
        await s.start()
        await asyncio.sleep(0.02)
        # Kill the loop the way the MCP failure did: an exception the handler
        # is not expected to survive, leaving `state` untouched at "running".
        s._task.cancel()
        try:
            await s._task
        except BaseException:
            pass
        s._task = _DeadTask(RuntimeError("stream closed"))
        s.state = "running"
        return s.status()

    status = asyncio.run(go())
    assert status["state"] != "running"
    assert "stream closed" in status["failed_reason"]


class _DeadTask:
    """A finished task that raised. Stands in for a loop that died."""
    def __init__(self, exc): self._exc = exc
    def done(self): return True
    def exception(self): return self._exc


def test_status_is_unchanged_while_the_loop_is_healthy():
    async def go():
        s = _sched(_Fund())
        await s.start()
        await asyncio.sleep(0.03)
        status = s.status()
        await s.stop()
        return status

    status = asyncio.run(go())
    assert status["state"] == "running"
    assert not status.get("failed_reason")


def test_a_never_started_scheduler_is_not_reported_as_failed():
    s = _sched(_Fund())
    assert s.status()["state"] == "stopped"
    assert not s.status().get("failed_reason")


def test_an_ordinary_stop_is_not_reported_as_a_death():
    """Cancellation is how STOP stops. Reporting it as a failure made every
    ordinary stop render "engine died" in the status pill — a fund that looked
    broken when nothing was wrong, which is its own kind of lie."""
    async def go():
        s = _sched(_Fund())
        await s.start()
        await asyncio.sleep(0.03)
        await s.stop()
        return s.status()

    status = asyncio.run(go())
    assert status["state"] == "stopped"
    assert status["failed_reason"] is None


def test_a_cancelled_task_left_behind_is_still_not_a_death():
    """Same rule when the task is cancelled without `stop()` tidying up."""
    async def go():
        s = _sched(_Fund())
        await s.start()
        await asyncio.sleep(0.02)
        s._task.cancel()
        try:
            await s._task
        except asyncio.CancelledError:
            pass
        s.state = "running"
        return s.status()

    status = asyncio.run(go())
    assert status["state"] == "stopped"
    assert status["failed_reason"] is None
