"""User-channel WebSocket subscriber.

Endpoint per docs.polymarket.com (researched 2026-05-13):
    wss://ws-subscriptions-clob.polymarket.com/ws/user

Subscribe message embeds the L2 creds:
    {"auth": {"apiKey": "...", "secret": "...", "passphrase": "..."},
     "markets": ["<condition_id>", ...], "type": "user"}

Event types we handle:
- `trade`  — one of our orders filled (full or partial)
- `order`  — order state change (open / cancelled / matched / rejected)

The subscriber drives events into a PositionTracker + OrderManager. The
auth payload is NEVER logged (CLAUDE.md rule #5); it lives only in the
subscribe message we send once.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

from live_market.websocket_client import USER_WS_URL


@dataclass(frozen=True)
class UserSubscriber:
    api_key: str
    secret: str
    passphrase: str
    markets: list[str] = field(default_factory=list)

    def subscribe_payload(self) -> str:
        return json.dumps({
            "auth": {
                "apiKey": self.api_key,
                "secret": self.secret,
                "passphrase": self.passphrase,
            },
            "markets": list(self.markets),
            "type": "user",
        })

    def __repr__(self) -> str:
        # Override default repr so creds never accidentally show up in logs.
        return f"UserSubscriber(markets={self.markets!r}, api_key=<redacted>)"


class _WSConnection(Protocol):
    async def send(self, text: str) -> None: ...
    async def __aiter__(self): ...
    async def close(self) -> None: ...


class UserWebSocketClient:
    """Async listener for fill/order events.

    Construction:
        client = UserWebSocketClient(
            on_trade=lambda ev: ...,
            on_order=lambda ev: ...,
            connect=my_connect_fn,   # injectable for tests
        )
    """

    def __init__(
        self,
        *,
        on_trade: Callable[[dict], None] | None = None,
        on_order: Callable[[dict], None] | None = None,
        url: str = USER_WS_URL,
        connect: Callable | None = None,
    ) -> None:
        self.on_trade = on_trade
        self.on_order = on_order
        self.url = url
        self._connect = connect or self._default_connect
        self._stop = asyncio.Event()

    async def run(self, sub: UserSubscriber) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(sub.subscribe_payload())
            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                events = msg if isinstance(msg, list) else [msg]
                for ev in events:
                    self._dispatch(ev)

    def stop(self) -> None:
        self._stop.set()

    def _dispatch(self, event: dict) -> None:
        kind = event.get("event_type") or event.get("type")
        if kind == "trade" and self.on_trade is not None:
            self.on_trade(event)
        elif kind == "order" and self.on_order is not None:
            self.on_order(event)

    @staticmethod
    def _default_connect(url: str):
        try:
            import websockets  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "websockets is not installed. Run `uv sync` to enable the live feed. "
                "Tests inject a fake connect()."
            ) from e
        return websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=2048)
