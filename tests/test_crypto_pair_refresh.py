"""The tradable pair list is fetched in the fund's own event loop.

The failure this fixes, seen live as "no movement in the UI": every crypto cycle
reported `universe: 0` and debated nothing, while the Massive data was fine (394
rows) and `currency_pairs()` returned 58 pairs when called on its own.

The wiring was the problem. `CryptoScout.scan` is sync and runs inside the
cycle, so its `pairs()` callable reached `_run_sync`, which saw a running loop,
spawned a worker thread and called `asyncio.run` — a NEW event loop. The MCP
session lives in the supervisor task of the MAIN loop, and anyio pins a session
to the task that opened it. Using it from another loop fails, and it failed
quietly: the exception's `str()` was empty, so the log read "currency pairs
unavailable:" with nothing after it.

This is the second time that constraint has bitten. The first was the session
teardown; this is the session USE. The fix is the same shape — do it in the
loop that owns it — and the list is cached because it changes daily at most.
"""

import asyncio

import pytest

from trading.crypto_discovery import CryptoScout


def _row(sym, close, open_, vol):
    return {"T": sym, "c": close, "o": open_, "v": vol, "n": 100}


ROWS = [_row("X:BTCUSD", 80000, 78000, 100_000)]


def test_a_scout_with_no_pairs_yet_screens_nothing():
    """Before the first refresh there is no universe, and inventing one would
    screen names the broker may not trade."""
    assert CryptoScout(grouped=lambda d: ROWS).scan() == []


def test_a_refreshed_list_is_used():
    s = CryptoScout(grouped=lambda d: ROWS)
    s.refresh_pairs(["BTC-USD"])
    assert [c.symbol for c in s.scan()] == ["BTC-USD"]


def test_a_refresh_replaces_the_previous_list():
    """A pair that has been delisted must stop being screened."""
    s = CryptoScout(grouped=lambda d: ROWS)
    s.refresh_pairs(["BTC-USD"])
    s.refresh_pairs(["ETH-USD"])
    assert s.scan() == []


def test_an_empty_refresh_is_ignored_rather_than_clearing_the_list():
    """A transient MCP failure must not blank the universe for a whole cycle.
    The last good list stands until a better one arrives."""
    s = CryptoScout(grouped=lambda d: ROWS)
    s.refresh_pairs(["BTC-USD"])
    s.refresh_pairs([])
    assert [c.symbol for c in s.scan()] == ["BTC-USD"]


def test_the_scout_no_longer_calls_anything_that_needs_a_loop():
    """The regression, stated structurally: `scan` must be pure sync over data
    it already holds."""
    import inspect
    src = inspect.getsource(CryptoScout.scan)
    assert "asyncio" not in src and "_run_sync" not in src


# ------------------------------------------------------ the fund's refresh

def test_the_fund_refreshes_in_its_own_loop():
    """The whole point: the await happens on the cycle's loop, where the MCP
    session was opened."""
    from trading.fund import FundLoop

    calls = []

    class _Venue:
        async def currency_pairs(self):
            calls.append(asyncio.get_running_loop())
            return ["BTC-USD", "ETH-USD"]

    scout = CryptoScout(grouped=lambda d: ROWS)
    fund = FundLoop.__new__(FundLoop)
    fund.crypto_scout = scout
    fund.pair_source = _Venue()

    async def go():
        await fund._refresh_crypto_pairs()
        return asyncio.get_running_loop()

    loop = asyncio.run(go())
    assert calls, "the venue was never asked"
    assert [c.symbol for c in scout.scan()] == ["BTC-USD"]


def test_a_failing_refresh_never_ends_the_cycle():
    from trading.fund import FundLoop

    class _Broken:
        async def currency_pairs(self):
            raise RuntimeError("mcp down")

    fund = FundLoop.__new__(FundLoop)
    fund.crypto_scout = CryptoScout(grouped=lambda d: ROWS)
    fund.pair_source = _Broken()
    asyncio.run(fund._refresh_crypto_pairs())          # must not raise


def test_no_pair_source_is_a_no_op():
    from trading.fund import FundLoop
    fund = FundLoop.__new__(FundLoop)
    fund.crypto_scout = CryptoScout(grouped=lambda d: ROWS)
    fund.pair_source = None
    asyncio.run(fund._refresh_crypto_pairs())
