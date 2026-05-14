"""Polymarket market-channel WebSocket subscriber.

Endpoint per docs.polymarket.com (2026-05-13):
    wss://ws-subscriptions-clob.polymarket.com/ws/market   (no auth)
    wss://ws-subscriptions-clob.polymarket.com/ws/user     (auth required)

Subscribe message:
    {"assets_ids": ["<token_id>", ...], "type": "market"}

Incoming event types: book, price_change, last_trade_price, best_bid_ask.

This module is asyncio-based. The connection layer is injectable so tests
don't need the `websockets` package installed. Production use requires
`pip install websockets` (or `uv add websockets`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Protocol

from live_market.orderbook_cache import OrderBookCache


MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


class _WSConnection(Protocol):
    async def send(self, text: str) -> None: ...
    async def __aiter__(self) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...


@dataclass
class MarketSubscriber:
    """Configuration for a market-channel subscription."""
    token_ids: list[str]
    custom_feature_enabled: bool = True

    def subscribe_payload(self) -> str:
        return json.dumps({
            "assets_ids": list(self.token_ids),
            "type": "market",
            "custom_feature_enabled": self.custom_feature_enabled,
        })


class MarketWebSocketClient:
    """Read-only market channel listener that updates an OrderBookCache.

    Construction:
        client = MarketWebSocketClient(cache, connect=my_connect_fn)

    Where `connect(url)` returns an async context-manager yielding a
    _WSConnection. Default `connect` uses the `websockets` package; tests
    pass a fake.
    """

    def __init__(
        self,
        cache: OrderBookCache,
        *,
        url: str = MARKET_WS_URL,
        connect: Callable | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.cache = cache
        self.url = url
        self._connect = connect or self._default_connect
        self._on_event = on_event
        self._stop = asyncio.Event()

    async def run(self, sub: MarketSubscriber) -> None:
        """Open the socket, subscribe, drive events into the cache until stop()."""
        async with self._connect(self.url) as ws:
            await ws.send(sub.subscribe_payload())
            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                # Some servers wrap in lists; flatten.
                events = msg if isinstance(msg, list) else [msg]
                for ev in events:
                    self.cache.apply_event(ev)
                    if self._on_event is not None:
                        self._on_event(ev)

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _default_connect(url: str):
        try:
            import websockets  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "websockets is not installed. Run `uv add websockets` to enable the live feed. "
                "Tests inject a fake connect()."
            ) from e
        return websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=2048)
