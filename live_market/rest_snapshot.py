"""Seed the orderbook cache from a REST snapshot.

The WebSocket emits incremental updates. To know the full state, you must
first pull a snapshot via REST. This module does that — once per token, on
session start.
"""

from __future__ import annotations

from live_market.orderbook_cache import BookLevel, OrderBookCache


def seed_from_snapshot(cache: OrderBookCache, token_id: str, snapshot: dict) -> None:
    """Apply a REST `get_order_book` response to the cache.

    The py-clob-client snapshot shape is roughly:
        {"market": "...", "asset_id": "...", "bids": [{"price": "0.5", "size": "100"}, ...],
         "asks": [{"price": "0.51", "size": "200"}, ...]}
    """
    book = cache.get(token_id)
    bids = [BookLevel(float(x["price"]), float(x["size"])) for x in snapshot.get("bids", [])]
    asks = [BookLevel(float(x["price"]), float(x["size"])) for x in snapshot.get("asks", [])]
    book.apply_book_snapshot(bids, asks)
