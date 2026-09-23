"""The shadow resolver must never block the loop it is scored from.

The failure this pins is not a wrong number, it is a dead process. Measured at
02:44 on 2026-09-22, six minutes after a restart, with `sample`:

    task_step_impl                      <- a coroutine, on the event loop
      lock_PyThread_acquire_lock
        _PySemaphore_Wait               <- 2225 of 2225 samples

`ShadowResolver` took a SYNC `quote` callable. The fund supplied one that
reached the async venue, so `dashboard.fund_wiring._run_sync` saw a running
loop and did `pool.submit(asyncio.run, coro).result()` — the coroutine on a
worker thread's brand-new loop, this loop blocked on the result with no
timeout. The venue is Robinhood over MCP and its session is pinned to the
CYCLE's loop, so the coroutine could never finish. The block was therefore
permanent: no cycles, no HTTP, no health endpoint, until the process was
killed. `/api/health` timed out at 15s; CPU sat at 0%.

The fix is the one `_refresh_crypto_pairs` already uses: the async caller
fetches on the loop that owns the session and hands the values in. What is
pinned here is that the blocking shape cannot come back.
"""

import asyncio
import inspect
import time

import pytest

from roundtable.shadow import ShadowResolver


class _Store:
    def __init__(self, rows): self.rows, self.written = rows, []
    def recent_deliberations(self, limit=200): return self.rows
    def resolved_outcomes(self, limit=400): return []
    def record_thesis_outcome(self, *a, **k): self.written.append((a, k))


def _row(tid, symbol, age_hours, price=100.0, signal="bullish"):
    return {"thesis_id": tid, "symbol": symbol, "status": "complete",
            "created": time.time() - age_hours * 3600, "signal": signal,
            "payload": {"price": price, "consensus": {"signal": signal}}}


# ---------- the shape that wedged the process cannot be rebuilt ----------

def test_the_fund_builds_a_resolver_with_no_sync_quote_callable():
    """A sync callable is how a coroutine reached a worker thread's loop. The
    fund must not hand one over; it fetches the prices itself."""
    import dashboard.fund_wiring as wiring

    src = inspect.getsource(wiring)
    assert "ShadowResolver(memory=memory)" in src, "the fund passes no quote"
    assert "_run_sync" not in src, "the blocking bridge is gone"
    assert ".result()" not in src, "nothing waits on a future from the loop"


def test_scoring_is_awaited_and_prices_come_from_the_cycles_own_loop():
    from trading.fund import FundLoop

    assert inspect.iscoroutinefunction(FundLoop._score_past_calls)
    src = inspect.getsource(FundLoop._score_past_calls)
    assert "await self._quotes(" in src, "fetched on the loop that owns it"
    assert "prices=prices" in src, "and handed in, not fetched inside"


def test_a_resolver_with_no_quote_and_no_prices_scores_nothing_quietly():
    """Refusing to score beats reaching for a price by any means available."""
    store = _Store([_row("t1", "BTC-USD", age_hours=30)])
    assert ShadowResolver(memory=store).resolve_due() == 0
    assert store.written == []


# ---------- due_symbols and resolve_due cannot drift apart ----------

def test_due_symbols_names_exactly_what_resolve_due_would_score():
    """If they diverged, the cycle would fetch one set of prices and the
    resolver would want another — every sample silently skipped."""
    store = _Store([
        _row("t1", "BTC-USD", age_hours=30),
        _row("t2", "ETH-USD", age_hours=30),
        _row("t3", "SOL-USD", age_hours=2),      # inside the horizon
        _row("t4", "DOGE-USD", age_hours=10_000),  # past max_age
    ])
    r = ShadowResolver(memory=store)
    assert set(r.due_symbols()) == {"BTC-USD", "ETH-USD"}
    assert r.resolve_due(prices={"BTC-USD": 110.0, "ETH-USD": 90.0}) == 2


def test_due_symbols_deduplicates():
    """Two debates on one name are one quote, not two."""
    store = _Store([_row("t1", "BTC-USD", age_hours=30),
                    _row("t2", "BTC-USD", age_hours=40)])
    assert ShadowResolver(memory=store).due_symbols() == ("BTC-USD",)


def test_a_symbol_missing_from_prices_is_skipped_not_scored_flat():
    """A provider blip must not become a sample scored against a price we
    never saw — that is a fabricated outcome in the calibration record."""
    store = _Store([_row("t1", "BTC-USD", age_hours=30),
                    _row("t2", "ETH-USD", age_hours=30)])
    assert ShadowResolver(memory=store).resolve_due(prices={"BTC-USD": 110.0}) == 1


def test_an_unreadable_memory_yields_no_symbols_rather_than_raising():
    """`due_symbols` runs inside a cycle. A failure to learn must not become a
    failure to trade."""
    class _Broken:
        def recent_deliberations(self, limit=200): raise RuntimeError("db gone")
        def resolved_outcomes(self, limit=400): return []
    assert ShadowResolver(memory=_Broken()).due_symbols() == ()
    assert ShadowResolver(memory=_Broken()).resolve_due(prices={}) == 0


# ---------- the loop stays responsive while a quote is slow ----------

def test_a_slow_quote_does_not_stall_the_loop():
    """The regression in one assertion. A venue that takes a second to answer
    must cost a second of WAITING, not a second of the loop being unable to
    run anything else — which is what `.result()` on a worker's future did."""
    from trading.fund import FundLoop

    class _Q:
        mid = 110.0

    loop_ran = []

    async def slow_quotes(symbols):
        await asyncio.sleep(0.05)
        return {s: _Q() for s in symbols}

    async def heartbeat():
        for _ in range(10):
            await asyncio.sleep(0.005)
            loop_ran.append(1)

    class _Report:
        scored_calls = 0

    store = _Store([_row("t1", "BTC-USD", age_hours=30)])
    fund = object.__new__(FundLoop)
    fund.shadow = ShadowResolver(memory=store)
    fund._quotes = slow_quotes

    async def main():
        await asyncio.gather(FundLoop._score_past_calls(fund, _Report()),
                             heartbeat())

    asyncio.run(main())
    assert len(loop_ran) == 10, "the loop kept running while the quote was out"
    assert len(store.written) == 1


# ---------- a legacy row's price is fetched under a name the venue knows ----------

def test_a_bare_crypto_base_is_quoted_as_a_pair():
    """Ten deliberations sat due and unscored for 30 hours because they were
    recorded as `BTC` and `ETH` — the bare form the config override uses. The
    venue spells every pair `BASE-USD` and cannot quote a bare base, so the
    price came back empty every cycle and the row was skipped exactly as a
    provider blip is. They would have aged out silently, taking ten samples out
    of a calibration set that needs thirty to switch on."""
    from roundtable.shadow import quote_symbol

    assert quote_symbol({"symbol": "BTC", "asset_class": "crypto"}) == "BTC-USD"
    assert quote_symbol({"symbol": "eth", "asset_class": "crypto"}) == "ETH-USD"


def test_a_pair_that_is_already_spelled_out_is_untouched():
    from roundtable.shadow import quote_symbol
    assert quote_symbol({"symbol": "BTC-USD", "asset_class": "crypto"}) == "BTC-USD"


def test_an_equity_ticker_never_gains_a_quote_currency():
    """No US ticker contains a hyphen, and appending one would invent an
    instrument."""
    from roundtable.shadow import quote_symbol
    assert quote_symbol({"symbol": "AAPL", "asset_class": "equity"}) == "AAPL"
    assert quote_symbol({"symbol": "SMH", "asset_class": None}) == "SMH"


def test_an_empty_symbol_is_not_a_pair():
    from roundtable.shadow import quote_symbol
    assert quote_symbol({"symbol": "", "asset_class": "crypto"}) is None
    assert quote_symbol({}) is None


def test_the_legacy_row_is_asked_for_and_scored_under_one_name():
    """The failure mode if these drifted: the cycle fetches BTC-USD and the
    resolver looks up BTC, so every sample is skipped forever."""
    store = _Store([_row("t1", "BTC", age_hours=30)])
    store.rows[0]["asset_class"] = "crypto"
    r = ShadowResolver(memory=store)

    assert r.due_symbols() == ("BTC-USD",)
    assert r.resolve_due(prices={"BTC-USD": 110.0}) == 1, \
        "the price must be found under the name that was asked for"
    assert store.written, "and the outcome recorded"


def test_the_outcome_keeps_the_symbol_it_was_recorded_under():
    """Only the QUOTE lookup is normalised. Rewriting the stored symbol would
    split one instrument's history across two names."""
    store = _Store([_row("t1", "BTC", age_hours=30)])
    store.rows[0]["asset_class"] = "crypto"
    ShadowResolver(memory=store).resolve_due(prices={"BTC-USD": 110.0})
    args, kwargs = store.written[0]
    assert "BTC" in args, args
    assert "BTC-USD" not in args, "the record keeps the original spelling"


def test_a_row_with_no_entry_price_is_unscoreable_not_due():
    """Ten rows written before deliberations stored `price` or `evidence` have
    nothing to measure a move against. Treating them as due made the cycle
    fetch a quote for them every five minutes forever, for work that could
    never happen. Inventing the entry is the one thing worse: a fabricated
    sample in the set the seats are calibrated on."""
    store = _Store([_row("t1", "BTC-USD", age_hours=30)])
    store.rows[0]["payload"] = {"consensus": {"signal": "bullish"}}   # no price
    r = ShadowResolver(memory=store)
    assert r.due_symbols() == ()
    assert r.resolve_due(prices={"BTC-USD": 110.0}) == 0
    assert store.written == []


def test_a_row_with_an_entry_price_is_still_due():
    store = _Store([_row("t1", "BTC-USD", age_hours=30, price=100.0)])
    r = ShadowResolver(memory=store)
    assert r.due_symbols() == ("BTC-USD",)
    assert r.resolve_due(prices={"BTC-USD": 110.0}) == 1


# ---------- a non-quorate table is not a committee prediction ----------

def _seats(live, total=7):
    return [{"failed": i >= live} for i in range(total)]


def test_a_one_seat_deliberation_is_not_scored():
    """Measured 2026-09-23 with both providers rate-limited: three
    deliberations completed with ONE live seat of seven. TSM came back
    "bullish, confidence 50" on the Sentiment Analyst alone; STX the same on
    the Risk Manager alone. Which seat survived was decided by whichever won
    the rate-limit race.

    `ThesisPipeline` already refuses to TRADE those — "a thesis carried by one
    surviving seat is not a committee decision" — and it is right. Scoring
    applied no such test, so every seat would have been graded 24 hours later
    against a call one seat made."""
    store = _Store([_row("t1", "TSM", age_hours=30, price=100.0)])
    store.rows[0]["payload"]["opinions"] = _seats(live=1)
    r = ShadowResolver(memory=store)
    assert r.due_symbols() == ()
    assert r.resolve_due(prices={"TSM": 110.0}) == 0
    assert store.written == []


def test_a_quorate_deliberation_is_still_scored():
    from trading.pipeline import MIN_RESPONDING_SEATS

    store = _Store([_row("t1", "TSM", age_hours=30, price=100.0)])
    store.rows[0]["payload"]["opinions"] = _seats(live=MIN_RESPONDING_SEATS)
    r = ShadowResolver(memory=store)
    assert r.due_symbols() == ("TSM",)
    assert r.resolve_due(prices={"TSM": 110.0}) == 1


def test_the_quorum_is_the_pipelines_own_constant():
    """One definition. A scoring quorum that drifted from the trading quorum
    would grade the committee on calls it was never allowed to act on, or
    refuse to grade calls it did act on."""
    import inspect

    from roundtable import shadow
    from trading.pipeline import MIN_RESPONDING_SEATS

    src = inspect.getsource(shadow._is_quorate)
    assert "from trading.pipeline import MIN_RESPONDING_SEATS" in src
    assert "live >= MIN_RESPONDING_SEATS" in src, "compared, not re-stated"

    # And it moves with the pipeline's number rather than a copy of it.
    store = _Store([_row("t1", "TSM", age_hours=30, price=100.0)])
    store.rows[0]["payload"]["opinions"] = _seats(live=MIN_RESPONDING_SEATS - 1)
    assert ShadowResolver(memory=store).due_symbols() == ()


def test_a_row_with_no_opinions_recorded_is_still_scored():
    """Old rows pre-date the field. Refusing them all would discard real
    history to guard against a contamination that never happened in them."""
    store = _Store([_row("t1", "BTC-USD", age_hours=30, price=100.0)])
    store.rows[0]["payload"].pop("opinions", None)
    assert ShadowResolver(memory=store).resolve_due(prices={"BTC-USD": 110.0}) == 1
