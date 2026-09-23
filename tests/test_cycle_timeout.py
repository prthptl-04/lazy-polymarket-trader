"""A cycle that never returns must not stop the fund forever.

Measured 2026-09-22 10:08, during the equity open the whole night was building
toward:

    last_cycle_at   09:21:27      (2008 seconds earlier)
    next_cycle_in   0.0
    state           running
    errors          0

A cycle had started around 09:26 and was still running 42 minutes later. Its
five deliberations had all COMPLETED — seven opinions each — and the last model
call was 60 minutes old, so it was not thinking. It was hung AFTER the
thinking, awaiting network I/O that never returned. The event loop sat idle in
`select`: no CPU burned, no exception raised, nothing to catch.

So the engine reported itself healthy and slept through the open.

`_run` already catches `BaseException` rather than `Exception`, because the MCP
client raises `BaseExceptionGroup` when its stream dies and that once left the
engine "running" for eleven hours. This is the same silent death by a different
route, and the lesson generalises: A SUPERVISOR THAT ONLY CATCHES EXCEPTIONS
CANNOT SEE A COROUTINE THAT NEVER RETURNS. It needs a clock as well as a
try/except.

`asyncio.wait_for` cancels what it wraps, so the stuck await is torn down and
the next cycle starts from a clean read of the account.
"""

import asyncio

import pytest

from trading.fund_scheduler import (
    CYCLE_TIMEOUT_SECONDS,
    DEFAULT_CYCLE_SECONDS,
    FundScheduler,
)


def _scheduler(**kw):
    s = object.__new__(FundScheduler)
    s.metrics = type(s).__dataclass_fields__["metrics"].default_factory() \
        if "metrics" in getattr(type(s), "__dataclass_fields__", {}) else None
    from trading.fund_scheduler import SchedulerMetrics
    s.metrics = SchedulerMetrics()
    s.cycle_interval_seconds = kw.get("interval", 0.01)
    s.cycle_timeout_seconds = kw.get("timeout", 0.05)
    s.failed_reason = None
    s.state = "running"
    return s


# ---------- the budget itself ----------

def test_the_timeout_is_generous_against_a_real_cycle():
    """Five deliberations is ~35 model calls and one took seven minutes
    overnight. Cutting a slow cycle off would be worse than the hang."""
    assert CYCLE_TIMEOUT_SECONDS == DEFAULT_CYCLE_SECONDS * 4
    assert CYCLE_TIMEOUT_SECONDS >= 7 * 60 * 2


# ---------- a hung cycle is cancelled and reported ----------

def test_a_hung_cycle_is_cancelled_and_counted_as_an_error():
    calls = {"started": 0, "cancelled": 0}

    async def hang():
        calls["started"] += 1
        try:
            await asyncio.Event().wait()          # never returns
        except asyncio.CancelledError:
            calls["cancelled"] += 1
            raise

    sched = _scheduler()
    sched.run_once = hang
    stops = iter([False, True])
    sched._should_stop = lambda: next(stops, True)

    async def _sleep(_): return None
    sched._sleep = _sleep

    asyncio.run(FundScheduler._run(sched))

    assert calls["started"] == 1
    assert calls["cancelled"] == 1, "the stuck await must be torn down"
    assert sched.metrics.errors == 1
    assert "hung, not slow" in sched.metrics.last_error


def test_the_loop_survives_the_timeout_and_runs_the_next_cycle():
    """The point of cancelling rather than dying: one hang costs one cycle."""
    seen = []

    async def sometimes_hangs():
        seen.append(len(seen))
        if len(seen) == 1:
            await asyncio.Event().wait()
        return "ok"

    sched = _scheduler()
    sched.run_once = sometimes_hangs
    stops = iter([False, False, True])
    sched._should_stop = lambda: next(stops, True)

    async def _sleep(_): return None
    sched._sleep = _sleep

    asyncio.run(FundScheduler._run(sched))

    assert len(seen) == 2, "the cycle after the hang still ran"
    assert sched.metrics.errors == 1
    assert sched.failed_reason is None, "a hang is not a dead loop"


def test_a_cycle_that_refuses_to_die_does_not_hang_the_supervisor():
    """The regression, and the reason `wait_for` was not enough.

    On 2026-09-22 DNS dropped, the MCP session's anyio task group was torn
    down from the wrong task, and cancelling the cycle raised "Attempted to
    exit cancel scope in a different task than it was entered in" from inside
    the unwind. The task never finished dying. `asyncio.wait_for` asks for
    cancellation and then BLOCKS until the task completes, so it never
    returned and the handler that logs the timeout never ran — 54 minutes past
    a 20-minute budget, no timeout recorded anywhere, `errors` reading 3.

    Here the cycle swallows cancellation and keeps running, exactly as a
    broken cancel scope does. The supervisor must still get out.
    """
    refusals = {"n": 0}

    async def undying():
        # Swallows cancellation the way a broken anyio unwind does. Bounded so
        # the loop can still shut down at the end of the test — the real one
        # was unbounded, which is why it had to be abandoned rather than
        # awaited.
        for _ in range(40):
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                refusals["n"] += 1
                continue

    sched = _scheduler(timeout=0.05)
    sched.run_once = undying
    stops = iter([False, True])
    sched._should_stop = lambda: next(stops, True)

    async def _sleep(_): return None
    sched._sleep = _sleep

    async def main():
        # The whole assertion: this returns at all.
        await asyncio.wait_for(FundScheduler._run(sched), timeout=5.0)

    asyncio.run(main())

    assert refusals["n"] >= 1, "the cycle must actually have refused to die"
    assert sched.metrics.errors == 1
    assert "abandoned" in sched.metrics.last_error
    assert sched.failed_reason is None, "an abandoned cycle is not a dead loop"


def test_the_supervisor_does_not_await_the_abandoned_task():
    """Stated as code, because the difference between `wait_for` and this is
    one await and the entire failure."""
    import inspect

    src = inspect.getsource(FundScheduler._run)
    assert "asyncio.wait(" in src
    assert "asyncio.wait_for(" not in src, \
        "wait_for blocks on a task that will not finish dying"
    after_cancel = src.split("task.cancel()")[1]
    assert "await task" not in after_cancel


def test_a_normal_cycle_is_untouched():
    ran = []

    async def quick():
        ran.append(1)
        return "ok"

    sched = _scheduler()
    sched.run_once = quick
    stops = iter([False, True])
    sched._should_stop = lambda: next(stops, True)

    async def _sleep(_): return None
    sched._sleep = _sleep

    asyncio.run(FundScheduler._run(sched))
    assert ran == [1]
    assert sched.metrics.errors == 0
    assert sched.metrics.last_error is None


def test_a_raising_cycle_still_reports_its_own_exception():
    """The timeout must not swallow the error path that already worked."""
    async def boom():
        raise RuntimeError("venue exploded")

    sched = _scheduler()
    sched.run_once = boom
    stops = iter([False, True])
    sched._should_stop = lambda: next(stops, True)

    async def _sleep(_): return None
    sched._sleep = _sleep

    asyncio.run(FundScheduler._run(sched))
    assert sched.metrics.errors == 1
    assert "RuntimeError" in sched.metrics.last_error
    assert "venue exploded" in sched.metrics.last_error


def test_stopping_still_propagates_through_the_timeout():
    """CancelledError is how STOP stops. `wait_for` must not convert an
    operator's cancellation into a timeout error."""
    async def slow():
        await asyncio.Event().wait()

    sched = _scheduler(timeout=60.0)
    sched.run_once = slow
    sched._should_stop = lambda: False

    async def _sleep(_): return None
    sched._sleep = _sleep

    async def main():
        task = asyncio.create_task(FundScheduler._run(sched))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert sched.metrics.errors == 0, "a STOP is not a fault"
