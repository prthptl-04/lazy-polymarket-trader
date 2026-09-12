"""Market data behind one interface.

Everything upstream asks a `MarketDataProvider` for bars, quotes and
financials. Nothing upstream knows where they came from, which matters because
the eventual source is still undecided: Robinhood's MCP surface is unverified
(see OPEN_NOTES), and the alternatives each carry a dependency cost worth
choosing deliberately rather than by accident.

Two implementations ship here, both dependency-free:

- `StaticProvider` — data handed in up front. Real and useful: it drives tests,
  backtests, and replay of recorded sessions.
- `VenueQuoteProvider` — live quotes from any `VenueAdapter`, so the fund gets
  real prices today via Robinhood or the paper venue. It has no history and no
  fundamentals, and says so rather than inventing them.

A provider that cannot supply something returns `None`. That is not a failure
path — `Candidate.evidence_block()` reports absent screens as NOT AVAILABLE and
the seats are instructed to treat them as unknown. Degrading loudly beats
guessing quietly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

from finance.exits import Bar
from finance.quality import Financials
from trading.venues.base import Quote


@dataclass(frozen=True)
class PriceHistory:
    """Bars plus the derived series the screens need."""

    bars: tuple[Bar, ...]
    closes: tuple[float, ...] = ()
    volumes: tuple[float, ...] = ()

    @property
    def returns(self) -> list[float]:
        return [
            (self.closes[i] - self.closes[i - 1]) / self.closes[i - 1]
            for i in range(1, len(self.closes))
            if self.closes[i - 1]
        ]

    @property
    def dollar_volumes(self) -> list[float]:
        """Aligned with `returns`, so Amihud gets equal-length inputs."""
        return [c * v for c, v in zip(self.closes[1:], self.volumes[1:])]


class MarketDataProvider(Protocol):
    """What the candidate builder needs to do its job."""

    async def get_quote(self, symbol: str) -> Optional[Quote]: ...

    async def get_history(self, symbol: str, *, lookback: int = 60) -> Optional[PriceHistory]: ...

    async def get_financials(
        self, symbol: str
    ) -> Optional[tuple[Financials, Optional[Financials]]]: ...


@dataclass
class StaticProvider:
    """Data supplied up front. Deterministic, so tests assert exact numbers."""

    quotes: dict[str, Quote] = field(default_factory=dict)
    histories: dict[str, PriceHistory] = field(default_factory=dict)
    financials: dict[str, tuple[Financials, Optional[Financials]]] = field(default_factory=dict)

    def set_history(
        self, symbol: str, bars: Sequence[Bar], volumes: Sequence[float] | None = None
    ) -> None:
        closes = tuple(b.close for b in bars)
        vols = tuple(volumes) if volumes else tuple(1_000_000.0 for _ in bars)
        self.histories[symbol] = PriceHistory(
            bars=tuple(bars), closes=closes, volumes=vols
        )

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        return self.quotes.get(symbol)

    async def get_history(self, symbol: str, *, lookback: int = 60) -> Optional[PriceHistory]:
        history = self.histories.get(symbol)
        if history is None:
            return None
        if lookback and len(history.bars) > lookback:
            return PriceHistory(
                bars=history.bars[-lookback:],
                closes=history.closes[-lookback:],
                volumes=history.volumes[-lookback:],
            )
        return history

    async def get_financials(
        self, symbol: str
    ) -> Optional[tuple[Financials, Optional[Financials]]]:
        return self.financials.get(symbol)


@dataclass
class VenueQuoteProvider:
    """Live quotes from a venue adapter; optional history from a fallback.

    Use this to get real prices today. It deliberately does not fabricate
    history or fundamentals — `get_history` and `get_financials` delegate to
    `fallback` if one is given, and otherwise return None so the screens that
    need them are reported absent.
    """

    adapter: Any
    fallback: Optional[MarketDataProvider] = None

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        try:
            return await self.adapter.get_quote(symbol)
        except Exception:
            # A dead quote feed must degrade to "unknown", not crash the cycle.
            return None

    async def get_history(self, symbol: str, *, lookback: int = 60) -> Optional[PriceHistory]:
        if self.fallback is None:
            return None
        return await self.fallback.get_history(symbol, lookback=lookback)

    async def get_financials(
        self, symbol: str
    ) -> Optional[tuple[Financials, Optional[Financials]]]:
        if self.fallback is None:
            return None
        return await self.fallback.get_financials(symbol)
