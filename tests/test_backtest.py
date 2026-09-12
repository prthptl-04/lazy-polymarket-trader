"""Backtest engine.

The tests that matter here are the anti-lying ones. A backtester that reports
returns the account could never have earned is worse than no backtester.

- Blind: no look-ahead, fills happen at the NEXT bar's open, PDT and session
  gates apply to history.
- Acceptance: a buy-and-hold on a rising series makes roughly the right money.
- Edge: too few candles, warmup, no orders, last bar can't fill.
"""

from datetime import datetime, timedelta

import pytest

from backtest.engine import Backtester, BacktestConfig, BarContext, Candle
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest


def _session_days(n: int, start=datetime(2026, 9, 14, 10, 0, tzinfo=EASTERN)):
    """n consecutive weekday timestamps at 10:00 ET (regular session)."""
    out, cursor = [], start
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _candles(prices, timestamps=None):
    ts = timestamps or _session_days(len(prices))
    return [
        Candle(timestamp=t, open=p, high=p * 1.01, low=p * 0.99, close=p, volume=1_000_000)
        for t, p in zip(ts, prices)
    ]


def _cfg(**kw):
    kw.setdefault("symbol", "TEST")
    kw.setdefault("starting_cash_usd", 10_000.0)
    kw.setdefault("spread_bps", 0)
    kw.setdefault("slippage_bps", 0)
    kw.setdefault("warmup_bars", 0)
    return BacktestConfig(**kw)


# ---------------- anti-look-ahead ----------------

@pytest.mark.asyncio
async def test_strategy_cannot_see_the_future():
    seen = []

    def strategy(ctx: BarContext):
        seen.append((ctx.index, len(ctx.history)))
        return None

    candles = _candles([100] * 10)
    await Backtester(_cfg()).run(candles, strategy)

    # History length always equals index+1 — never more.
    for index, length in seen:
        assert length == index + 1


@pytest.mark.asyncio
async def test_history_last_bar_is_the_current_bar():
    captured = []

    def strategy(ctx: BarContext):
        captured.append((ctx.current.close, ctx.now))
        return None

    candles = _candles([10, 20, 30, 40, 50])
    await Backtester(_cfg()).run(candles, strategy)
    assert [c for c, _ in captured] == [10, 20, 30, 40]   # last bar can't trade


# ---------------- fill timing ----------------

@pytest.mark.asyncio
async def test_order_fills_at_next_bar_open_not_current_close():
    """Deciding on bar i and filling at bar i's close is free money."""
    def strategy(ctx: BarContext):
        if ctx.index == 0:
            return [OrderRequest(symbol="TEST", side="buy",
                                 asset_class="equity", quantity=1)]
        return None

    # Bar 0 closes at 100; bar 1 opens at 200.
    ts = _session_days(3)
    candles = [
        Candle(ts[0], open=100, high=100, low=100, close=100, volume=1),
        Candle(ts[1], open=200, high=200, low=200, close=200, volume=1),
        Candle(ts[2], open=200, high=200, low=200, close=200, volume=1),
    ]
    result = await Backtester(_cfg()).run(candles, strategy)

    assert len(result.fills) == 1
    assert result.fills[0]["price"] == pytest.approx(200.0)   # not 100


@pytest.mark.asyncio
async def test_last_bar_produces_no_orders():
    calls = []

    def strategy(ctx: BarContext):
        calls.append(ctx.index)
        return None

    candles = _candles([100] * 5)
    await Backtester(_cfg()).run(candles, strategy)
    assert max(calls) == 3          # index 4 is the last bar, never consulted


# ---------------- gates apply to history ----------------

@pytest.mark.asyncio
async def test_overnight_round_trips_are_never_blocked_by_pdt():
    """Alternating buy/sell across separate days are NOT day trades, so a
    swing strategy must never be throttled — that is the whole reason the fund
    trades this horizon."""
    def strategy(ctx: BarContext):
        side = "buy" if ctx.position_quantity == 0 else "sell"
        return [OrderRequest(symbol="TEST", side=side,
                             asset_class="equity", quantity=1)]

    candles = _candles([100] * 30)        # one bar per weekday
    result = await Backtester(
        _cfg(starting_cash_usd=5_000.0, account_equity_for_pdt_usd=5_000.0)
    ).run(candles, strategy)

    assert result.metrics()["rejected_by_gate"].get("pdt", 0) == 0
    assert len(result.fills) > 10         # it really did trade throughout


@pytest.mark.asyncio
async def test_same_day_round_trips_hit_the_pdt_gate():
    day = datetime(2026, 9, 14, 10, 0, tzinfo=EASTERN)
    # Eight bars inside ONE trading day → repeated same-day round trips.
    ts = [day + timedelta(minutes=30 * i) for i in range(12)]
    candles = _candles([100] * 12, timestamps=ts)

    def strategy(ctx: BarContext):
        side = "buy" if ctx.position_quantity == 0 else "sell"
        return [OrderRequest(symbol="TEST", side=side,
                             asset_class="equity", quantity=1)]

    result = await Backtester(
        _cfg(starting_cash_usd=5_000.0, account_equity_for_pdt_usd=5_000.0)
    ).run(candles, strategy)

    assert result.metrics()["rejected_by_gate"].get("pdt", 0) > 0


@pytest.mark.asyncio
async def test_weekend_equity_orders_are_rejected_by_the_session_gate():
    sat = datetime(2026, 9, 19, 12, 0, tzinfo=EASTERN)
    ts = [sat + timedelta(hours=i) for i in range(6)]
    candles = _candles([100] * 6, timestamps=ts)

    def strategy(ctx: BarContext):
        return [OrderRequest(symbol="TEST", side="buy",
                             asset_class="equity", quantity=1)]

    result = await Backtester(_cfg()).run(candles, strategy)
    assert result.metrics()["rejected_by_gate"].get("session", 0) > 0
    assert result.fills == []


# ---------------- returns ----------------

@pytest.mark.asyncio
async def test_buy_and_hold_tracks_the_price():
    def strategy(ctx: BarContext):
        if ctx.index == 0:
            return [OrderRequest(symbol="TEST", side="buy",
                                 asset_class="equity", notional_usd=1_000.0)]
        return None

    candles = _candles([100, 100, 110, 120, 130])
    result = await Backtester(_cfg()).run(candles, strategy)

    # ~10 shares bought at 100, ending at 130 → ~$300 gain on $10k.
    assert result.total_return_pct == pytest.approx(3.0, abs=0.2)
    assert result.final_equity_usd > result.equity_curve[0]


@pytest.mark.asyncio
async def test_doing_nothing_keeps_equity_flat():
    result = await Backtester(_cfg()).run(_candles([100, 110, 90, 105]), lambda ctx: None)
    assert result.equity_curve == [10_000.0] * 4
    assert result.total_return_pct == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_slippage_and_spread_cost_money():
    def strategy(ctx: BarContext):
        if ctx.index == 0:
            return [OrderRequest(symbol="TEST", side="buy",
                                 asset_class="equity", quantity=10)]
        return None

    candles = _candles([100] * 5)
    clean = await Backtester(_cfg()).run(candles, strategy)
    costly = await Backtester(
        _cfg(spread_bps=100, slippage_bps=100)
    ).run(candles, strategy)

    assert costly.final_equity_usd < clean.final_equity_usd


@pytest.mark.asyncio
async def test_commission_is_charged_per_fill():
    def strategy(ctx: BarContext):
        if ctx.index == 0:
            return [OrderRequest(symbol="TEST", side="buy",
                                 asset_class="equity", quantity=1)]
        return None

    candles = _candles([100] * 4)
    free = await Backtester(_cfg()).run(candles, strategy)
    paid = await Backtester(_cfg(commission_per_order_usd=50.0)).run(candles, strategy)

    assert free.final_equity_usd - paid.final_equity_usd == pytest.approx(50.0)


# ---------------- warmup + degenerate input ----------------

@pytest.mark.asyncio
async def test_warmup_suppresses_early_orders():
    calls = []

    def strategy(ctx: BarContext):
        calls.append(ctx.index)
        return None

    await Backtester(_cfg(warmup_bars=5)).run(_candles([100] * 10), strategy)
    assert min(calls) == 5


@pytest.mark.asyncio
async def test_too_few_candles_returns_empty():
    result = await Backtester(_cfg()).run(_candles([100]), lambda ctx: None)
    assert result.equity_curve == []
    assert result.metrics()["bars"] == 0


@pytest.mark.asyncio
async def test_metrics_shape():
    result = await Backtester(_cfg()).run(_candles([100, 105, 95, 110]), lambda ctx: None)
    m = result.metrics()
    for key in ("total_return_pct", "sharpe", "max_drawdown_pct",
                "var_95_pct", "cvar_95_pct", "fills", "rejections", "bars"):
        assert key in m


# ---------------- context helpers ----------------

@pytest.mark.asyncio
async def test_context_exposes_session_and_pdt_budget():
    captured = []

    def strategy(ctx: BarContext):
        captured.append((ctx.session, ctx.pdt_day_trades_remaining))
        return None

    await Backtester(_cfg()).run(_candles([100] * 4), strategy)
    assert captured[0][0] == "regular"
    assert captured[0][1] == 3


@pytest.mark.asyncio
async def test_context_returns_are_derived_from_closes():
    captured = []

    def strategy(ctx: BarContext):
        if ctx.index == 2:
            captured.append(ctx.returns)
        return None

    await Backtester(_cfg()).run(_candles([100, 110, 121, 130]), strategy)
    assert captured[0] == pytest.approx([0.1, 0.1])
