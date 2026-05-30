"""Autonomous trading loop — the GO/STOP runner.

Owns NO trading math. Its only job is scheduling:

  - Market-channel WS task: drives OrderBookCache (already async producer)
  - User-channel WS task: drives PositionTracker (already async producer)
  - Strategy tick: per `tick_interval_seconds`, ask strategy to
    submit_or_replace for each watched (market_id, token_id)
  - Cashout tick: per `cashout_interval_seconds`, ask CashoutEngine for
    signals; submit grader-passed counter-orders
  - Status loop: per `status_interval_seconds`, push a snapshot to the
    dashboard hub

Lifecycle:
  state: "stopped" → "starting" → "running" → "stopping" → "stopped"

State changes are idempotent — calling start() while running is a no-op;
stop() while stopped is a no-op.

The loop NEVER bypasses the Outcome Grader or the live-trading 5-gate check
in `trading.execution.Executor`. Paper-mode autonomy is the default; live
mode still requires the .env + paper-trade-count + approval-lesson gates.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional


LoopState = Literal["stopped", "starting", "running", "stopping"]


@dataclass(frozen=True)
class WatchedMarket:
    market_id: str
    token_id: str


@dataclass
class LoopMetrics:
    started_at: Optional[float] = None
    strategy_ticks: int = 0
    cashout_ticks: int = 0
    submissions: int = 0
    replacements: int = 0
    cashout_submissions: int = 0
    skips: int = 0
    errors: int = 0
    last_error: Optional[str] = None


@dataclass
class AutonomousLoop:
    """Schedules trading ticks. Construct, then `await start()` / `await stop()`."""

    strategy: Any
    order_manager: Any
    cashout_engine: Any
    position_tracker: Any
    cache: Any
    watched: list[WatchedMarket]

    tick_interval_seconds: float = 0.5
    cashout_interval_seconds: float = 1.0
    status_interval_seconds: float = 0.5

    on_status: Optional[Callable[[dict], None]] = None
    on_decision: Optional[Callable[[Any], None]] = None
    on_cashout: Optional[Callable[[Any], None]] = None

    market_ws_task_factory: Optional[Callable[[], "asyncio.Future"]] = None
    user_ws_task_factory: Optional[Callable[[], "asyncio.Future"]] = None

    state: LoopState = "stopped"
    metrics: LoopMetrics = field(default_factory=LoopMetrics)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _stop_event: asyncio.Event | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self.state in ("running", "starting"):
            return
        self.state = "starting"
        self._stop_event = asyncio.Event()
        self.metrics = LoopMetrics(started_at=time.time())

        if self.market_ws_task_factory is not None:
            self._tasks.append(asyncio.create_task(self.market_ws_task_factory()))
        if self.user_ws_task_factory is not None:
            self._tasks.append(asyncio.create_task(self.user_ws_task_factory()))
        self._tasks.append(asyncio.create_task(self._strategy_loop()))
        self._tasks.append(asyncio.create_task(self._cashout_loop()))
        self._tasks.append(asyncio.create_task(self._status_loop()))

        self.state = "running"

    async def stop(self) -> None:
        if self.state in ("stopped", "stopping"):
            return
        self.state = "stopping"
        if self._stop_event is not None:
            self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        # Wait for tasks to actually settle; suppress CancelledError noise.
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self.state = "stopped"

    def status(self) -> dict:
        return {
            "state": self.state,
            "started_at": self.metrics.started_at,
            "uptime_seconds": (time.time() - self.metrics.started_at) if self.metrics.started_at else 0.0,
            "watched_count": len(self.watched),
            "open_orders": len(self.order_manager.open_orders()) if hasattr(self.order_manager, "open_orders") else 0,
            "open_positions": len(self.position_tracker.all_open()) if hasattr(self.position_tracker, "all_open") else 0,
            "metrics": {
                "strategy_ticks": self.metrics.strategy_ticks,
                "cashout_ticks": self.metrics.cashout_ticks,
                "submissions": self.metrics.submissions,
                "replacements": self.metrics.replacements,
                "cashout_submissions": self.metrics.cashout_submissions,
                "skips": self.metrics.skips,
                "errors": self.metrics.errors,
                "last_error": self.metrics.last_error,
            },
        }

    # ---------- inner loops ----------

    async def _strategy_loop(self) -> None:
        while not self._should_stop():
            for w in self.watched:
                try:
                    decision = await self.strategy.submit_or_replace_async(
                        w.token_id, w.market_id, self.order_manager,
                    )
                    self._record_decision(decision)
                except Exception as e:
                    self._record_error(f"strategy: {type(e).__name__}: {e}")
            self.metrics.strategy_ticks += 1
            await self._sleep(self.tick_interval_seconds)

    async def _cashout_loop(self) -> None:
        while not self._should_stop():
            try:
                signals = self.cashout_engine.evaluate_all()
                for sig in signals:
                    if not sig.grade_passed:
                        continue
                    placed = await self.order_manager.submit_async(sig.counter_trade)
                    if placed.status == "open":
                        self.metrics.cashout_submissions += 1
                    if self.on_cashout is not None:
                        self.on_cashout(sig)
            except Exception as e:
                self._record_error(f"cashout: {type(e).__name__}: {e}")
            self.metrics.cashout_ticks += 1
            await self._sleep(self.cashout_interval_seconds)

    async def _status_loop(self) -> None:
        while not self._should_stop():
            if self.on_status is not None:
                try:
                    self.on_status(self.status())
                except Exception:
                    pass    # status push failure must not kill the loop
            await self._sleep(self.status_interval_seconds)

    # ---------- helpers ----------

    def _record_decision(self, decision: Any) -> None:
        kind = getattr(decision, "kind", None)
        if kind == "submit":
            self.metrics.submissions += 1
        elif kind == "replace":
            self.metrics.replacements += 1
        elif kind == "skip":
            self.metrics.skips += 1
        if self.on_decision is not None:
            try:
                self.on_decision(decision)
            except Exception:
                pass

    def _record_error(self, msg: str) -> None:
        self.metrics.errors += 1
        self.metrics.last_error = msg

    def _should_stop(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
