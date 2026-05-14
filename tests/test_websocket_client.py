import asyncio
import json

import pytest

from live_market.orderbook_cache import OrderBookCache
from live_market.websocket_client import (
    MARKET_WS_URL,
    MarketSubscriber,
    MarketWebSocketClient,
)


class _FakeWS:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()

    async def close(self) -> None:
        pass


class _FakeConnect:
    def __init__(self, ws: _FakeWS) -> None:
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


def _connect_factory(ws: _FakeWS):
    def _connect(url: str):
        return _FakeConnect(ws)
    return _connect


def test_subscriber_payload_shape():
    sub = MarketSubscriber(token_ids=["tok-a", "tok-b"])
    msg = json.loads(sub.subscribe_payload())
    assert msg["type"] == "market"
    assert msg["assets_ids"] == ["tok-a", "tok-b"]
    assert msg["custom_feature_enabled"] is True


def test_market_ws_url_matches_docs():
    assert MARKET_WS_URL == "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def test_client_subscribes_and_applies_events():
    events = [
        json.dumps({
            "event_type": "book", "asset_id": "tok-a",
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.41", "size": "150"}],
        }),
        json.dumps({
            "event_type": "price_change", "asset_id": "tok-a",
            "side": "ASK", "price": "0.405", "size": "75",
        }),
    ]
    ws = _FakeWS(events)
    cache = OrderBookCache()
    client = MarketWebSocketClient(cache, connect=_connect_factory(ws))

    asyncio.run(client.run(MarketSubscriber(["tok-a"])))

    assert ws.sent and json.loads(ws.sent[0])["type"] == "market"
    book = cache.get("tok-a")
    assert any(abs(level.price - 0.405) < 1e-9 for level in book.asks)


def test_client_tolerates_non_json_frame():
    events = ["not json", json.dumps({"event_type": "book", "asset_id": "tok-a", "bids": [], "asks": []})]
    ws = _FakeWS(events)
    cache = OrderBookCache()
    client = MarketWebSocketClient(cache, connect=_connect_factory(ws))
    asyncio.run(client.run(MarketSubscriber(["tok-a"])))
    assert cache.has("tok-a")


def test_default_connect_raises_with_helpful_message_if_websockets_missing(monkeypatch):
    import importlib.util
    if importlib.util.find_spec("websockets") is not None:
        pytest.skip("websockets is installed in this env; negative test only meaningful when missing")
    with pytest.raises(RuntimeError, match="websockets is not installed"):
        MarketWebSocketClient._default_connect(MARKET_WS_URL)
