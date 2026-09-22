"""GO / STOP driver for the fund loop.

`FundLoop.run_cycle` does one cycle. This decides when cycles happen, and it is
the component that finally arms the kill-switch in the live path: before every
cycle it reads the account from the venue and hands the mark to
`FundLoop.run_cycle`, which observes it before anything is decided.

Until this existed, `DailyLossKillSwitch` was enforced in the router and the
backtester but nothing fed it in production — a switch that exists but is never
shown the equity reads as protection you do not have.

Lifecycle mirrors `trading.autonomous_loop.AutonomousLoop` so the dashboard's
GO/STOP semantics are identical:

    stopped → starting → running → stopping → stopped

Both transitions are idempotent. STOP cancels the cycle task; it does **not**
cancel orders already resting at the venue — those persist until filled or
cancelled explicitly, same as rule #18.

A STOP mid-deliberation leaves the thesis `in_progress` in memory rather than
losing it. `FundLoop.resume_unfinished()` surfaces those on the next GO; what
to do with them is a policy decision, because a thesis built on week-old prices
should be abandoned rather than acted on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from trading.fund import CycleReport, Holding
from trading.sessions import EASTERN, session_at

logger = logging.getLogger(__name__)

SchedulerState = Literal["stopped", "starting", "running", "stopping"]

# Raised at the loop rather than handled by it. `CancelledError` is how STOP
# stops; `KeyboardInterrupt` and `SystemExit` are operator intent, and a loop
# that absorbs Ctrl-C cannot be killed. Everything else — including
# `BaseExceptionGroup`, which is NOT an `Exception` and which killed this loop
# silently for eleven hours — is caught, counted, and survived.
NEVER_SWALLOW: tuple[type[BaseException], ...] = (
    asyncio.CancelledError, KeyboardInterrupt, SystemExit,
)


def is_operator_intent(exc: BaseException) -> bool:
    """True for the failures the cycle loop must NOT absorb."""
    return isinstance(exc, NEVER_SWALLOW)

DEFAULT_CYCLE_SECONDS = 300.0       # 5 minutes — swing horizon, not HFT


@dataclass
class SchedulerMetrics:
    started_at: Optional[float] = None
    cycles: int = 0
    submitted: int = 0
    halted: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    last_cycle_at: Optional[float] = None


@dataclass
class FundScheduler:
    """Runs fund cycles on an interval until stopped."""

    fund: Any                                  # FundLoop
    venue: Any                                 # VenueAdapter, for account + positions
    cycle_interval_seconds: float = DEFAULT_CYCLE_SECONDS
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).astimezone(EASTERN)
    on_cycle: Optional[Callable[[CycleReport], None]] = None
    on_status: Optional[Callable[[dict], None]] = None
    # Persists the fund blob at the end of each cycle. This is what captures
    # the kill-switch baseline and the PDT ledger, neither of which goes
    # through the position book's own save points.
    save_state: Optional[Callable[[], None]] = None

    state: SchedulerState = "stopped"
    metrics: SchedulerMetrics = field(default_factory=SchedulerMetrics)
    last_report: Optional[CycleReport] = None
    resumable: list[str] = field(default_factory=list)

    # Set when the cycle loop exits without being asked to. `status()` reads it
    # so a dead scheduler can never report itself as running.
    failed_reason: Optional[str] = None

    _task: Optional[asyncio.Task] = None
    _stop_event: Optional[asyncio.Event] = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self.state in ("running", "starting"):
            return
        self.state = "starting"
        self._stop_event = asyncio.Event()
        self.failed_reason = None
        self.metrics = SchedulerMetrics(started_at=time.time())

        # Surface work interrupted by the last STOP. Deliberately reported, not
        # auto-resumed — see the module docstring.
        try:
            self.resumable = await self.fund.resume_unfinished()
        except Exception:
            logger.exception("failed to read unfinished deliberations")
            self.resumable = []

        self._task = asyncio.create_task(self._run())
        self.state = "running"
        self._publish()

    async def stop(self) -> None:
        if self.state in ("stopped", "stopping"):
            return
        self.state = "stopping"
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.state = "stopped"
        self._publish()

    # ---------- the loop ----------

    async def _run(self) -> None:
        """The cycle loop. Survives anything a cycle can throw at it.

        Catches BaseException, not Exception. That is deliberate and was learned
        the expensive way: the Robinhood MCP client raises `BaseExceptionGroup`
        when its stream dies, which does NOT derive from `Exception`. It escaped
        this handler, killed the task, and left `state` reading "running" for
        eleven hours while nothing cycled and no stop was enforced.

        Three still propagate, because absorbing them is worse than dying:
        `CancelledError` is how STOP stops, and `KeyboardInterrupt` /
        `SystemExit` are operator intent — a loop that swallows Ctrl-C cannot
        be killed.
        """
        try:
            while not self._should_stop():
                try:
                    await self.run_once()
                except NEVER_SWALLOW:
                    raise
                except BaseException as e:
                    self.metrics.errors += 1
                    self.metrics.last_error = f"{type(e).__name__}: {e}"
                    logger.exception("fund cycle failed")
                await self._sleep(self.cycle_interval_seconds)
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            # The loop is over and nobody asked. Record it so `status()` can
            # say so rather than reporting the state it was left in.
            self.failed_reason = f"cycle loop died: {type(e).__name__}: {e}"
            logger.exception("fund cycle loop exited unexpectedly")
            raise

    async def run_once(self) -> CycleReport:
        """One cycle against live account state. Public so it can be triggered."""
        moment = self.clock()
        equity, cash, holdings = await self._read_account()

        report = await self.fund.run_cycle(
            moment,
            holdings=holdings,
            equity_usd=equity,
            available_cash_usd=cash,
        )

        self.last_report = report
        self.metrics.cycles += 1
        self.metrics.submitted += len(report.submitted)
        if report.halted_reason:
            self.metrics.halted += 1
        if report.errors:
            self.metrics.errors += len(report.errors)
            self.metrics.last_error = report.errors[-1]
        self.metrics.last_cycle_at = time.time()

        if self.save_state is not None:
            try:
                self.save_state()
            except Exception:
                logger.exception("could not persist fund state after the cycle")

        if self.on_cycle is not None:
            try:
                self.on_cycle(report)
            except Exception:
                logger.exception("on_cycle callback failed")
        self._publish()
        return report

    # ---------- account ----------

    async def _read_account(self) -> tuple[Optional[float], Optional[float], list[Holding]]:
        """Equity, cash and positions from the venue.

        Failures degrade to `None` rather than raising: the kill-switch treats
        an unobserved day as unarmed and says so, which is a better outcome
        than a crashed scheduler that stops trading silently.
        """
        equity = cash = None
        holdings: list[Holding] = []
        try:
            snapshot = await self.venue.account()
            equity, cash = snapshot.equity_usd, snapshot.buying_power_usd
        except Exception:
            logger.exception("could not read account snapshot")
        try:
            holdings = [
                Holding(symbol=p.symbol, asset_class=p.asset_class, quantity=p.quantity)
                for p in await self.venue.positions()
            ]
        except Exception:
            logger.exception("could not read positions")
        return equity, cash, holdings

    # ---------- status ----------

    def _liveness(self) -> tuple[str, Optional[str]]:
        """The real state, read from the TASK rather than from a stored string.

        `self.state` is set at start and stop. If the loop dies in between,
        nothing updates it — which is exactly how a dead scheduler reported
        itself healthy for an entire trading session. This checks the object
        that actually does the work.
        """
        if self.state != "running" or self._task is None:
            return self.state, self.failed_reason
        if not self._task.done():
            return "running", None
        reason = self.failed_reason
        if reason is None:
            try:
                exc = self._task.exception()
            except asyncio.CancelledError:
                # Cancellation is how STOP stops. Reporting it as a death made
                # every ordinary stop render as "engine died" and, because the
                # pill keyed off `failed_reason`, made the fund look broken
                # when nothing was wrong.
                return "stopped", None
            except asyncio.InvalidStateError:
                return "running", None          # not finished after all
            reason = (f"cycle loop died: {type(exc).__name__}: {exc}"
                      if exc else "cycle loop exited without an error")
        return "stopped", reason

    def status(self) -> dict:
        moment = self.clock()
        session = session_at(moment)
        uptime = (
            time.time() - self.metrics.started_at if self.metrics.started_at else 0.0
        )
        kill_switch = getattr(self.fund, "kill_switch", None)
        pdt = getattr(getattr(self.fund, "router", None), "pdt", None)
        state, failed_reason = self._liveness()
        return {
            "state": state,
            # Present only when the loop stopped without being asked to. A
            # dashboard that cannot distinguish "stopped" from "died" will
            # show a healthy fund with a dead engine.
            "failed_reason": failed_reason,
            "session": session.value,
            "equities_open": session.equities_open,
            "uptime_seconds": round(uptime, 1),
            "cycle_interval_seconds": self.cycle_interval_seconds,
            # Five minutes between cycles means a four-second poll shows the
            # same numbers seventy-five times in a row. A countdown is the
            # difference between "working" and "hung".
            "next_cycle_in_seconds": self._next_cycle_in(),
            "resumable_theses": list(self.resumable),
            "metrics": {
                "cycles": self.metrics.cycles,
                "submitted": self.metrics.submitted,
                "halted": self.metrics.halted,
                "errors": self.metrics.errors,
                "last_error": self.metrics.last_error,
                "last_cycle_at": self.metrics.last_cycle_at,
            },
            "kill_switch": kill_switch.status(moment) if kill_switch else None,
            "router_live_gate": (
                router.live_gate.status()
                if (router := getattr(getattr(self, "fund", None), "router", None))
                and getattr(router, "live_gate", None) else None),
            "pdt": pdt.status(moment) if pdt else None,
            "last_cycle": self.last_report.summary() if self.last_report else None,
        }

    # ---------- helpers ----------

    def _next_cycle_in(self) -> Optional[float]:
        """Seconds until the next cycle, or None when nothing is scheduled."""
        if self._liveness()[0] != "running":
            return None
        if self.metrics.last_cycle_at is None:
            return 0.0
        elapsed = time.time() - self.metrics.last_cycle_at
        return round(max(0.0, self.cycle_interval_seconds - elapsed), 1)

    def _publish(self) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(self.status())
        except Exception:
            logger.exception("on_status callback failed")

    def _should_stop(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    async def _sleep(self, seconds: float) -> None:
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
