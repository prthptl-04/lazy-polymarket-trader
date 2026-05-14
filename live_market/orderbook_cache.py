"""In-memory orderbook cache.

Designed for sub-millisecond reads: every method that the strategy hot-path
calls is O(1) or O(log n). The WebSocket subscriber writes; the strategy
reads. No locks — Python's GIL serializes dict ops; we keep updates atomic
per call.

For real concurrency (multi-process or true threading) wrap calls in an
asyncio.Lock per token. We do NOT do that here because the trading loop is
single-asyncio-task by design.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BookLevel:
    price: float        # 0 < price < 1
    size: float         # USD value at this level


@dataclass
class OrderBook:
    token_id: str
    bids: list[BookLevel] = field(default_factory=list)   # sorted descending by price
    asks: list[BookLevel] = field(default_factory=list)   # sorted ascending by price
    updated_at: float = 0.0
    last_trade_price: float | None = None

    # ---- hot-path reads ----

    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    def midpoint(self) -> float | None:
        b = self.best_bid()
        a = self.best_ask()
        if b is None or a is None:
            return None
        return (b.price + a.price) / 2.0

    def spread_bps(self) -> int | None:
        b = self.best_bid()
        a = self.best_ask()
        if b is None or a is None:
            return None
        mid = (a.price + b.price) / 2.0
        if mid <= 0:
            return None
        return int(round((a.price - b.price) / mid * 10_000))

    def depth_usd(self, side: str, levels: int = 5) -> float:
        book = self.bids if side == "YES" else self.asks
        return sum(level.size for level in book[:levels])

    # ---- writes ----

    def apply_book_snapshot(self, bids: list[BookLevel], asks: list[BookLevel]) -> None:
        self.bids = sorted(bids, key=lambda x: -x.price)
        self.asks = sorted(asks, key=lambda x: x.price)
        self.updated_at = time.monotonic()

    def apply_price_change(self, side: str, price: float, size: float) -> None:
        target = self.bids if side == "BID" else self.asks
        # Find existing level.
        for i, level in enumerate(target):
            if abs(level.price - price) < 1e-9:
                if size <= 0:
                    target.pop(i)
                else:
                    target[i] = BookLevel(price=price, size=size)
                self.updated_at = time.monotonic()
                return
        if size <= 0:
            return
        new_level = BookLevel(price=price, size=size)
        if side == "BID":
            # descending; insert preserving order
            keys = [-l.price for l in target]
            bisect.insort(keys, -price)
            target.insert(keys.index(-price), new_level)
        else:
            keys = [l.price for l in target]
            bisect.insort(keys, price)
            target.insert(keys.index(price), new_level)
        self.updated_at = time.monotonic()

    def apply_last_trade_price(self, price: float) -> None:
        self.last_trade_price = price
        self.updated_at = time.monotonic()


class OrderBookCache:
    """One book per `token_id`. Constant-time lookup."""

    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}

    def get(self, token_id: str) -> OrderBook:
        book = self._books.get(token_id)
        if book is None:
            book = OrderBook(token_id=token_id)
            self._books[token_id] = book
        return book

    def has(self, token_id: str) -> bool:
        return token_id in self._books

    def tokens(self) -> list[str]:
        return list(self._books.keys())

    def apply_event(self, event: dict) -> str | None:
        """Apply an incoming WebSocket event. Returns the token_id touched, or None.

        Event shapes per docs.polymarket.com (market channel):
            { "event_type": "book", "asset_id": "...", "bids": [...], "asks": [...] }
            { "event_type": "price_change", "asset_id": "...", "side": "BID"|"ASK",
              "price": "0.5", "size": "100" }
            { "event_type": "last_trade_price", "asset_id": "...", "price": "0.5" }
        """
        kind = event.get("event_type")
        token_id = event.get("asset_id")
        if not token_id:
            return None
        book = self.get(token_id)
        if kind == "book":
            bids = [BookLevel(float(x["price"]), float(x["size"])) for x in event.get("bids", [])]
            asks = [BookLevel(float(x["price"]), float(x["size"])) for x in event.get("asks", [])]
            book.apply_book_snapshot(bids, asks)
        elif kind == "price_change":
            book.apply_price_change(
                side=str(event["side"]).upper(),
                price=float(event["price"]),
                size=float(event["size"]),
            )
        elif kind == "last_trade_price":
            book.apply_last_trade_price(float(event["price"]))
        else:
            return None
        return token_id
