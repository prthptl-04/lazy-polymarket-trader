"""Supervised WebSocket feed tasks (Phase D).

`MarketWebSocketClient.run` / `UserWebSocketClient.run` each return when the
socket closes. In production a dropped socket is routine — a bare
`create_task(client.run(sub))` would silently stop feeding the cache and the
position tracker, and nothing upstream would notice.

This module wraps both clients in a supervisor that reconnects with capped
exponential backoff until cancelled. These are the coroutines
`AutonomousLoop.market_ws_task_factory` / `user_ws_task_factory` produce.

Async placement (CLAUDE.md rule #16): both runners are `async` producers.
Everything they call downstream — `OrderBookCache.apply_event`,
`PositionTracker.on_trade_event`, `OrderManager.on_order_event` — is µs-scale
sync, invoked inline.

Creds (CLAUDE.md rules #5 and #17): `derive_user_subscriber` reads the L2
creds off the client and hands them straight to `UserSubscriber`. They are
never logged, never persisted, and never included in a reconnect message.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from live_market.orderbook_cache import OrderBookCache
from live_market.user_channel import UserSubscriber, UserWebSocketClient
from live_market.websocket_client import MarketSubscriber, MarketWebSocketClient


# Reconnect backoff: 0.5s → 1 → 2 → 4 → 8 → 16 → 30 (capped).
INITIAL_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 30.0


class FeedStats:
    """Mutable counters a supervisor updates; read by status/observability."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.connects = 0
        self.disconnects = 0
        self.errors = 0
        self.last_error: Optional[str] = None

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "connects": self.connects,
            "disconnects": self.disconnects,
            "errors": self.errors,
            "last_error": self.last_error,
        }


async def _supervise(
    *,
    run_once: Callable[[], Any],
    stats: FeedStats,
    initial_backoff: float = INITIAL_BACKOFF_SECONDS,
    max_backoff: float = MAX_BACKOFF_SECONDS,
    max_attempts: Optional[int] = None,
) -> None:
    """Call `run_once()` forever, reconnecting on return or failure.

    Cancellation propagates (the loop's `stop()` cancels us). `max_attempts`
    exists so tests can bound the loop; production leaves it None.
    """
    backoff = initial_backoff
    attempts = 0
    while max_attempts is None or attempts < max_attempts:
        # Backoff precedes a *retry* only — the first attempt is immediate
        # and the last one is not followed by a dead wait.
        if attempts:
            await asyncio.sleep(backoff)
        attempts += 1
        try:
            stats.connects += 1
            await run_once()
            # Clean return == server closed the socket. Reconnect promptly.
            stats.disconnects += 1
            backoff = initial_backoff
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stats.errors += 1
            # Type + message only — never the payload, which carries creds.
            stats.last_error = f"{type(e).__name__}: {e}"
            backoff = min(backoff * 2, max_backoff)


async def run_market_feed(
    cache: OrderBookCache,
    token_ids: list[str],
    *,
    connect: Callable | None = None,
    url: str | None = None,
    stats: FeedStats | None = None,
    max_attempts: Optional[int] = None,
    initial_backoff: float = INITIAL_BACKOFF_SECONDS,
    max_backoff: float = MAX_BACKOFF_SECONDS,
) -> None:
    """Stream the public market channel into `cache` until cancelled."""
    stats = stats or FeedStats("market")
    sub = MarketSubscriber(token_ids=list(token_ids))

    async def _once() -> None:
        kwargs: dict[str, Any] = {"connect": connect}
        if url is not None:
            kwargs["url"] = url
        client = MarketWebSocketClient(cache, **kwargs)
        await client.run(sub)

    await _supervise(
        run_once=_once, stats=stats, max_attempts=max_attempts,
        initial_backoff=initial_backoff, max_backoff=max_backoff,
    )


async def run_user_feed(
    subscriber: UserSubscriber,
    *,
    on_trade: Callable[[dict], None] | None = None,
    on_order: Callable[[dict], None] | None = None,
    connect: Callable | None = None,
    url: str | None = None,
    stats: FeedStats | None = None,
    max_attempts: Optional[int] = None,
    initial_backoff: float = INITIAL_BACKOFF_SECONDS,
    max_backoff: float = MAX_BACKOFF_SECONDS,
) -> None:
    """Stream the authenticated user channel into the position/order handlers."""
    stats = stats or FeedStats("user")

    async def _once() -> None:
        kwargs: dict[str, Any] = {
            "on_trade": on_trade,
            "on_order": on_order,
            "connect": connect,
        }
        if url is not None:
            kwargs["url"] = url
        client = UserWebSocketClient(**kwargs)
        await client.run(subscriber)

    await _supervise(
        run_once=_once, stats=stats, max_attempts=max_attempts,
        initial_backoff=initial_backoff, max_backoff=max_backoff,
    )


def derive_user_subscriber(
    polymarket_client: Any,
    markets: list[str],
) -> Optional[UserSubscriber]:
    """Build a `UserSubscriber` from the client's L2 creds.

    Returns None when the client cannot produce creds — i.e. no wallet is
    configured. That is the normal paper-mode path: no wallet means no fills
    to listen for, so the user feed is simply not started rather than
    crashing the autonomous loop at GO.
    """
    derive = getattr(polymarket_client, "create_or_derive_api_creds", None)
    if derive is None:
        return None
    try:
        creds = derive()
    except Exception:
        # Missing key / missing funder / network failure — all mean "no user
        # feed". The exception text can carry env detail, so it is not logged.
        return None
    if creds is None:
        return None

    api_key = _cred(creds, "api_key", "apiKey", "key")
    secret = _cred(creds, "api_secret", "secret")
    passphrase = _cred(creds, "api_passphrase", "passphrase")
    if not (api_key and secret and passphrase):
        return None
    return UserSubscriber(
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        markets=list(markets),
    )


def _cred(creds: Any, *names: str) -> Optional[str]:
    """Read the first present attribute/key. py-clob-client uses the
    `api_*` names; dict-shaped creds from other paths use the bare ones."""
    for n in names:
        val = getattr(creds, n, None)
        if val is None and isinstance(creds, dict):
            val = creds.get(n)
        if val:
            return str(val)
    return None
