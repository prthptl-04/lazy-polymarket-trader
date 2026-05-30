"""User-channel subscriber + cancel_order primitive.

- Auditor: cred-redaction in __repr__, subscribe payload shape.
- Edge: dispatch with no callbacks; non-JSON frames; double subscribe.
- Blind: ensure creds aren't in the subscribe payload string keys.
"""

import asyncio
import json

import pytest

from live_market.user_channel import (
    USER_WS_URL,
    UserSubscriber,
    UserWebSocketClient,
)


# -------- subscribe payload --------

def test_subscribe_payload_shape():
    sub = UserSubscriber(api_key="k", secret="s", passphrase="p", markets=["m1", "m2"])
    payload = json.loads(sub.subscribe_payload())
    assert payload["type"] == "user"
    assert payload["markets"] == ["m1", "m2"]
    assert payload["auth"] == {"apiKey": "k", "secret": "s", "passphrase": "p"}


def test_user_ws_url_matches_docs():
    assert USER_WS_URL == "wss://ws-subscriptions-clob.polymarket.com/ws/user"


def test_subscriber_repr_redacts_creds():
    sub = UserSubscriber(api_key="sensitive-key", secret="sensitive-secret",
                         passphrase="sensitive-pass", markets=["m1"])
    rendered = repr(sub)
    assert "sensitive-key" not in rendered
    assert "sensitive-secret" not in rendered
    assert "sensitive-pass" not in rendered
    assert "redacted" in rendered.lower()


# -------- dispatch --------

class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
    async def send(self, text): self.sent.append(text)
    def __aiter__(self):
        async def gen():
            for f in self._frames:
                yield f
        return gen()
    async def close(self): pass


class _FakeConnect:
    def __init__(self, ws): self.ws = ws
    async def __aenter__(self): return self.ws
    async def __aexit__(self, *a): return False


def _connect_factory(ws):
    return lambda url: _FakeConnect(ws)


def test_trade_event_dispatched_to_callback():
    received = []
    ws = _FakeWS([
        json.dumps({"event_type": "trade", "market": "m1", "size": "10", "price": "0.4", "side": "YES"}),
    ])
    client = UserWebSocketClient(on_trade=received.append, connect=_connect_factory(ws))
    asyncio.run(client.run(UserSubscriber(api_key="k", secret="s", passphrase="p", markets=["m1"])))
    assert len(received) == 1
    assert received[0]["market"] == "m1"


def test_order_event_dispatched_separately():
    trades, orders = [], []
    ws = _FakeWS([
        json.dumps({"event_type": "order", "id": "o-1", "status": "open"}),
        json.dumps({"event_type": "trade", "market": "m1", "size": "1", "price": "0.5"}),
    ])
    client = UserWebSocketClient(
        on_trade=trades.append, on_order=orders.append, connect=_connect_factory(ws)
    )
    asyncio.run(client.run(UserSubscriber("k", "s", "p", ["m1"])))
    assert len(trades) == 1 and len(orders) == 1


def test_malformed_frame_does_not_crash():
    received = []
    ws = _FakeWS(["not-json", json.dumps({"event_type": "trade"}), "{broken"])
    client = UserWebSocketClient(on_trade=received.append, connect=_connect_factory(ws))
    asyncio.run(client.run(UserSubscriber("k", "s", "p", ["m1"])))
    assert len(received) == 1


def test_unknown_event_type_silently_ignored():
    received = []
    ws = _FakeWS([json.dumps({"event_type": "heartbeat"})])
    client = UserWebSocketClient(on_trade=received.append, connect=_connect_factory(ws))
    asyncio.run(client.run(UserSubscriber("k", "s", "p", ["m1"])))
    assert received == []


# -------- subscribe is sent FIRST --------

def test_subscribe_payload_is_first_message_sent():
    ws = _FakeWS([])
    client = UserWebSocketClient(connect=_connect_factory(ws))
    sub = UserSubscriber("k", "s", "p", ["m1"])
    asyncio.run(client.run(sub))
    assert ws.sent == [sub.subscribe_payload()]
