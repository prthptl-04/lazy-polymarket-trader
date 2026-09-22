"""Crypto scout — the fund screens the whole tradable crypto market.

`MarketScout` refuses a hand-typed equity watchlist on the grounds that it is
"a standing bet that the opportunity is where you last looked". Crypto had no
equivalent, so `FUND_CRYPTO_WATCHLIST` held BTC and ETH and every weekend cycle
put the same two instruments to a committee costing eight model calls each. Over
21 deliberations the committee declined all of them — correctly, on the evidence
it had. Two names is not a market.

Two sources, because neither alone is enough:

- **Robinhood's pair list** is what we can actually trade. 58 of 91 listed pairs
  are tradable today; screening one the broker will not sell us is a
  deliberation spent on nothing.
- **Massive's grouped crypto aggregate** is ONE call for ~392 tickers carrying
  volume, OHLC and trade count — the same shape the equity scout screens.

The intersection is the universe, and the ranking is the equity scout's
unchanged: volume outweighs the move, because a large move on thin volume is
usually one motivated buyer and a spread you cannot exit through.

Crypto trades continuously, so unlike the equity scout there is no weekday to
resolve — but the grouped endpoint still closes a daily bar, and asking for
today's before it closes returns a partial one. Yesterday is the last complete
session.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional, Sequence

from trading.discovery import ScoutCandidate

logger = logging.getLogger(__name__)

# Screens, and why each is here rather than being taste.
MIN_DOLLAR_VOLUME = 2_000_000.0   # an exit test, not a popularity one
MIN_ABS_MOVE_PCT = 1.0            # something has to be happening
MAX_ABS_MOVE_PCT = 40.0           # a name already up 40% has had its move
DEFAULT_LIMIT = 12

# Pegged to a dollar by construction: they cannot move enough to pay for a
# round trip, and their tiny moves would rank them on volume alone.
STABLECOINS = frozenset({
    "USDC", "USDT", "DAI", "PYUSD", "USDP", "TUSD", "BUSD", "FDUSD", "USDS",
})

_MASSIVE_TICKER = re.compile(r"^X:([A-Z0-9]+)USD$")


@dataclass
class CryptoScout:
    """Screens the tradable crypto market down to a handful worth debating."""

    # day -> grouped rows. Injected so tests never touch the network.
    grouped: Callable[[date], list[dict]]
    # Tradable pairs, refreshed by `refresh_pairs` from the fund's own event
    # loop. NOT a callable: it used to be, and the call reached the MCP session
    # from a worker thread's fresh event loop, which anyio refuses — quietly,
    # with an empty exception message, so every crypto cycle screened nothing
    # and the UI simply stopped moving.
    _pairs: tuple[str, ...] = ()

    min_dollar_volume: float = MIN_DOLLAR_VOLUME
    min_abs_move_pct: float = MIN_ABS_MOVE_PCT
    max_abs_move_pct: float = MAX_ABS_MOVE_PCT
    exclude: tuple[str, ...] = field(default_factory=tuple)

    def refresh_pairs(self, pairs: Sequence[str]) -> None:
        """Replace the tradable list. Called from the fund's event loop.

        An EMPTY refresh is ignored rather than applied: a transient MCP failure
        must not blank the universe for a whole cycle, and the last good list is
        a better answer than none.
        """
        if pairs:
            self._pairs = tuple(pairs)

    def scan(self, *, day: Optional[date] = None,
             limit: int = DEFAULT_LIMIT) -> list[ScoutCandidate]:
        """The screen. Deterministic, and runs before any LLM sees anything."""
        # Failing closed: without a pair list we would screen 392 tickers
        # against a broker that trades 58 of them, and most deliberations would
        # be on names we cannot buy.
        tradable = {s.upper() for s in self._pairs}
        if not tradable:
            return []

        try:
            rows = self.grouped(day or self._last_complete_day())
        except Exception:
            logger.exception("grouped crypto aggregate unavailable")
            return []

        out = self._screen(rows or [], tradable)
        return out[:limit]

    def _screen(self, rows: list[dict], tradable: set[str]) -> list[ScoutCandidate]:
        excluded = {s.upper() for s in self.exclude}
        out: list[ScoutCandidate] = []
        for r in rows:
            symbol = _to_pair(str(r.get("T") or ""))
            if symbol is None or symbol not in tradable or symbol in excluded:
                continue
            base = symbol.split("-")[0]
            if base in STABLECOINS:
                continue

            close, open_, volume = r.get("c"), r.get("o"), r.get("v")
            if close is None or open_ in (None, 0) or volume is None:
                continue
            dollar_volume = float(close) * float(volume)
            if dollar_volume < self.min_dollar_volume:
                continue
            move = (float(close) - float(open_)) / float(open_) * 100.0
            if not (self.min_abs_move_pct <= abs(move) <= self.max_abs_move_pct):
                continue

            # The equity scout's weighting, unchanged: a big move on thin
            # volume is usually one motivated buyer and a wide spread.
            score = (dollar_volume ** 0.5) * (1.0 + abs(move) / 100.0)
            out.append(ScoutCandidate(
                symbol=symbol, close=float(close), move_pct=move,
                dollar_volume=dollar_volume, trades=int(r.get("n") or 0),
                score=score,
                reason=f"{move:+.1f}% on ${dollar_volume / 1e6:,.0f}M traded",
            ))
        out.sort(key=lambda c: c.score, reverse=True)
        return out

    @staticmethod
    def _last_complete_day() -> date:
        """Crypto never closes, but the daily bar does. Today's is partial
        until it rolls, and a partial bar understates volume for every name
        equally — which quietly re-ranks the whole screen."""
        return date.today() - timedelta(days=1)


def _to_pair(massive_ticker: str) -> Optional[str]:
    """`X:BTCUSD` -> `BTC-USD`.

    Three spellings for one instrument — Massive's, Robinhood's, and the fund's
    bare `BTC` — and a mismatch between any two is how a universe comes back
    empty with nothing in the logs to say why.
    """
    m = _MASSIVE_TICKER.match(massive_ticker.upper())
    return f"{m.group(1)}-USD" if m else None


def _demo() -> None:
    assert _to_pair("X:BTCUSD") == "BTC-USD"
    assert _to_pair("X:WUSD") == "W-USD"
    assert _to_pair("AAPL") is None
    rows = [{"T": "X:BTCUSD", "c": 80000, "o": 78000, "v": 100_000, "n": 9},
            {"T": "X:DOGEUSD", "c": 0.4, "o": 0.3, "v": 1_000, "n": 3}]
    s = CryptoScout(grouped=lambda d: rows)
    s.refresh_pairs(["BTC-USD", "DOGE-USD"])
    picked = s.scan()
    assert [c.symbol for c in picked] == ["BTC-USD"], picked
    print("crypto_discovery self-check passed")


if __name__ == "__main__":
    _demo()
