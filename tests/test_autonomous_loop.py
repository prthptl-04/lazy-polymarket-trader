"""AutonomousLoop lifecycle + tick counters.

ACs (Acceptance Auditor):
- start/stop are idempotent
- status reports the right state at each phase
- strategy_ticks and cashout_ticks advance while running
- start() raises nothing when the loop has no WS factories

Edge Case Hunter:
- on_status / on_decision exceptions do not kill the loop
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from live_market.orderbook_cache import OrderBookCache
from trading.autonomous_loop import AutonomousLoop, WatchedMarket
from trading.position_tracker import PositionTracker


class _NoOpCashout:
    def evaluate_all(self): return []


class _NoOpOrderMgr:
    def open_orders(self): return []
    async def submit_async(self, trade):
        class _O:
            status = "open"
            local_id = "x"
        return _O()


class _StubStrategy:
    def __init__(self): self.calls = 0
    async def submit_or_replace_async(self, token_id, market_id, order_manager):
        self.calls += 1
        @dataclass(frozen=True)
        class _D:
            kind: str = "skip"
            reason: str = "stub"
            trade: Any = None
            submitted: Any = None
            replaced: Any = None
        return _D()


def _build_loop(**kwargs):
    cache = OrderBookCache()
    tracker = PositionTracker(cache=cache)
    return AutonomousLoop(
        strategy=_StubStrategy(),
        order_manager=_NoOpOrderMgr(),
        cashout_engine=_NoOpCashout(),
        position_tracker=tracker,
        cache=cache,
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        tick_interval_seconds=0.01,
        cashout_interval_seconds=0.01,
        status_interval_seconds=0.01,
        **kwargs,
    )


def test_start_transitions_through_states():
    async def run():
        loop = _build_loop()
        assert loop.state == "stopped"
        await loop.start()
        assert loop.state == "running"
        await asyncio.sleep(0.05)
        await loop.stop()
        assert loop.state == "stopped"
        assert loop.metrics.strategy_ticks > 0
        assert loop.metrics.cashout_ticks > 0
    asyncio.run(run())


def test_start_is_idempotent():
    async def run():
        loop = _build_loop()
        await loop.start()
        before = len(loop._tasks)
        await loop.start()
        assert len(loop._tasks) == before
        await loop.stop()
    asyncio.run(run())


def test_stop_is_idempotent():
    async def run():
        loop = _build_loop()
        await loop.stop()
        await loop.start()
        await loop.stop()
        await loop.stop()
        assert loop.state == "stopped"
    asyncio.run(run())


def test_status_callbacks_exception_doesnt_kill_loop():
    async def run():
        def boom(s): raise RuntimeError("client disconnected")
        loop = _build_loop(on_status=boom)
        await loop.start()
        await asyncio.sleep(0.05)
        assert loop.metrics.strategy_ticks > 0    # main loop survived
        await loop.stop()
    asyncio.run(run())


def test_status_dict_has_required_keys():
    loop = _build_loop()
    s = loop.status()
    assert {"state", "started_at", "uptime_seconds", "watched_count",
            "open_orders", "open_positions", "metrics"}.issubset(set(s))
