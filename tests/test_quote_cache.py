"""Serving the live feed without waiting on the broker.

Measured on the running dashboard: in-memory endpoints answer in 3-9ms, but
`/api/feeds` takes 367ms and `/api/balances` 217ms because each one calls the
Robinhood MCP. Polling those once a second would spend a third of every second
waiting on the network, and it gets worse with every symbol added — requests
would queue and the UI would feel slower the more it asked for.

So the read never waits. A cached value is returned immediately and a refresh
is kicked off behind it: stale-while-revalidate. The UI can then poll at 1Hz
and always get an answer in microseconds.

The tradeoff is honest and bounded: a quote may be up to `ttl` old. That is the
right trade for a display — a price one second stale shown instantly beats a
current price that arrives after the next poll has already fired. It is NOT the
right trade for execution, which is why `PaperVenue` and the router keep asking
the adapter directly and never read this.
"""

import time

import pytest

from dashboard.quote_cache import QuoteCache


class _Source:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def __call__(self, symbol):
        self.calls += 1
        if self.fail:
            raise RuntimeError("venue down")
        return f"quote-{symbol}-{self.calls}"


@pytest.mark.asyncio
async def test_the_first_read_fetches_and_returns():
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=5.0)
    assert await cache.get("BTC") == "quote-BTC-1"
    assert src.calls == 1


@pytest.mark.asyncio
async def test_a_fresh_read_never_touches_the_venue():
    """The whole point. At 1Hz polling this is the common case."""
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=5.0)
    await cache.get("BTC")
    for _ in range(20):
        assert await cache.get("BTC") == "quote-BTC-1"
    assert src.calls == 1, "twenty polls, one network call"


@pytest.mark.asyncio
async def test_a_stale_read_returns_immediately_and_refreshes_behind_it():
    """Stale-while-revalidate: the caller is never made to wait for the venue."""
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=0.01)
    await cache.get("BTC")
    time.sleep(0.02)

    assert await cache.get("BTC") == "quote-BTC-1", "the stale value, instantly"
    await cache.drain()
    assert await cache.get("BTC") == "quote-BTC-2", "refreshed behind the read"


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_serving_the_last_good_value():
    """A venue blip must not blank the display. The value is old, not absent,
    and an empty feed reads as a flat book."""
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=0.01)
    await cache.get("BTC")
    cache.fetch = _Source(fail=True)
    time.sleep(0.02)

    assert await cache.get("BTC") == "quote-BTC-1"
    await cache.drain()
    assert await cache.get("BTC") == "quote-BTC-1"


@pytest.mark.asyncio
async def test_a_first_read_that_fails_surfaces_the_real_error():
    """Nothing is cached, so there is no value to protect and the caller needs
    the true reason. `VenueError: not authorized` is a diagnosis; a swallowed
    None reaches the display as a blank row that says nothing."""
    cache = QuoteCache(fetch=_Source(fail=True), ttl_seconds=5.0)
    with pytest.raises(RuntimeError):
        await cache.get("BTC")


@pytest.mark.asyncio
async def test_concurrent_refreshes_of_one_symbol_collapse_into_one():
    """Ten pollers must not become ten calls to the broker."""
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=0.01)
    await cache.get("BTC")
    time.sleep(0.02)
    for _ in range(10):
        await cache.get("BTC")
    await cache.drain()
    assert src.calls == 2, f"expected one refresh, got {src.calls - 1}"


@pytest.mark.asyncio
async def test_symbols_are_cached_independently():
    src = _Source()
    cache = QuoteCache(fetch=src, ttl_seconds=5.0)
    assert await cache.get("BTC") == "quote-BTC-1"
    assert await cache.get("ETH") == "quote-ETH-2"
    assert await cache.get("BTC") == "quote-BTC-1"


@pytest.mark.asyncio
async def test_the_age_of_a_value_is_reportable():
    """A display that cannot say how old its number is will be believed more
    than it deserves."""
    cache = QuoteCache(fetch=_Source(), ttl_seconds=5.0)
    await cache.get("BTC")
    assert 0 <= cache.age("BTC") < 1.0
    assert cache.age("NEVER-FETCHED") is None
