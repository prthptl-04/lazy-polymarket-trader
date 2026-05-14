"""Feature extraction from a live OrderBook.

Features are intentionally cheap to compute — sub-microsecond per book. We
keep them in a small frozen dataclass so the predictor can hash/cache lookups.
"""

from __future__ import annotations

from dataclasses import dataclass

from live_market.orderbook_cache import OrderBook


@dataclass(frozen=True)
class MarketFeatures:
    token_id: str
    mid_price: float                 # in [0, 1]; the implied YES probability
    spread_bps: int
    depth_yes_usd: float             # 5-level depth on the bid side
    depth_no_usd: float              # 5-level depth on the ask side
    last_trade_price: float | None
    book_age_seconds: float          # how stale the book is right now

    # ---- convenience ----
    @property
    def has_liquidity(self) -> bool:
        return self.depth_yes_usd > 0 and self.depth_no_usd > 0


def extract_features(book: OrderBook, now: float | None = None) -> MarketFeatures | None:
    """Return features, or None if the book is too sparse to act on."""
    import time
    mid = book.midpoint()
    if mid is None:
        return None
    spread = book.spread_bps()
    if spread is None:
        return None
    return MarketFeatures(
        token_id=book.token_id,
        mid_price=mid,
        spread_bps=spread,
        depth_yes_usd=book.depth_usd("YES"),
        depth_no_usd=book.depth_usd("NO"),
        last_trade_price=book.last_trade_price,
        book_age_seconds=(now if now is not None else time.monotonic()) - book.updated_at,
    )
