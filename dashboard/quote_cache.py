"""Serve the live feed without waiting on the broker.

Measured on the running dashboard: in-memory endpoints answer in 3-9ms, while
`/api/feeds` takes 367ms and `/api/balances` 217ms, because each calls the
Robinhood MCP. Polling those once a second would spend a third of every second
waiting on the network — and worse with every symbol added, until requests
queue and the UI feels slower the more it asks for.

So a read never waits. The cached value is returned immediately and a refresh
runs behind it (stale-while-revalidate), which lets the dashboard poll at 1Hz
and still answer in microseconds.

The trade is bounded and deliberate: a displayed quote may be up to `ttl` old.
For a DISPLAY that is the right way round — a price one second stale shown
instantly beats a current price that arrives after the next poll has already
fired. It is the wrong way round for EXECUTION, which is why `PaperVenue` and
`VenueRouter` keep asking the adapter directly and never read this. The number
an order prices against must never be a cached one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# HALF the dashboard's 1Hz poll, deliberately.
#
# Stale-while-revalidate always serves the value it already has and refreshes
# behind the reader, so a fresh number lands one poll LATER. At a TTL equal to
# the poll interval that means the display moves every other tick, which reads
# as a stutter rather than as a feed. At half the interval every poll triggers
# a refresh and serves the previous one, so the number changes on every tick
# and trails reality by about a second.
#
# The cost is one refresh per symbol per second. Refreshes are per-symbol and
# concurrent, and the reader never waits for any of them — so adding symbols
# lengthens the refresh round rather than the response.
DEFAULT_TTL_SECONDS = 0.5


@dataclass
class QuoteCache:
    """One value per symbol, refreshed behind the reader."""

    fetch: Callable[[str], Awaitable[Any]]
    ttl_seconds: float = DEFAULT_TTL_SECONDS

    _values: dict[str, Any] = field(default_factory=dict)
    _stamps: dict[str, float] = field(default_factory=dict)
    _inflight: dict[str, asyncio.Task] = field(default_factory=dict)

    async def get(self, symbol: str) -> Any:
        """The freshest value we have, without ever blocking on the venue.

        Only a symbol that has never been fetched waits, because there is
        nothing to serve — and then a failure returns None rather than a guess.
        """
        age = self.age(symbol)
        if age is None:
            # Nothing cached, so there is no value to protect and the caller
            # needs the REAL reason — "VenueError: not authorized" is a
            # diagnosis, a swallowed None is not.
            await self._refresh(symbol, reraise=True)
            return self._values.get(symbol)
        if age > self.ttl_seconds:
            self._schedule(symbol)
        return self._values.get(symbol)

    def age(self, symbol: str) -> Optional[float]:
        """Seconds since this symbol was last fetched, or None if never.

        Exposed because a display that cannot say how old its number is will be
        believed more than it deserves.
        """
        stamp = self._stamps.get(symbol)
        return None if stamp is None else time.monotonic() - stamp

    async def drain(self) -> None:
        """Wait for refreshes in flight. For tests and shutdown, not for reads."""
        for task in list(self._inflight.values()):
            try:
                await task
            except Exception:
                pass

    # ---------- internals ----------

    def _schedule(self, symbol: str) -> None:
        # One refresh per symbol at a time: ten pollers must not become ten
        # calls to the broker.
        if symbol in self._inflight and not self._inflight[symbol].done():
            return
        try:
            self._inflight[symbol] = asyncio.create_task(self._refresh(symbol))
        except RuntimeError:
            pass        # no running loop; the next read will fetch inline

    async def _refresh(self, symbol: str, *, reraise: bool = False) -> None:
        try:
            value = await self.fetch(symbol)
        except Exception:
            # Keep serving the last good value. A venue blip must not blank the
            # display: the number is old, not absent, and an empty feed reads
            # as a flat book. On a COLD fetch there is nothing to keep, so the
            # error is the most useful thing we have and it propagates.
            logger.debug("quote refresh failed for %s", symbol, exc_info=True)
            if reraise:
                raise
            return
        self._values[symbol] = value
        self._stamps[symbol] = time.monotonic()
