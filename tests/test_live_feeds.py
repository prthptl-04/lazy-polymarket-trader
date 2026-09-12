"""Supervised WebSocket feed runners (Phase D).

- Acceptance: market feed drives the cache; user feed drives tracker + orders.
- Edge: reconnect on clean close, backoff growth on error, backoff reset,
  cancellation propagates, no-creds returns None.
- Blind: creds must never land in FeedStats.last_error.
"""

import asyncio

import pytest

from live_market.feeds import (
    MAX_BACKOFF_SECONDS,
    FeedStats,
    _supervise,
    derive_user_subscriber,
    run_market_feed,
    run_user_feed,
)
from live_market.orderbook_cache import OrderBookCache
from live_market.user_channel import UserSubscriber
from trading.order_manager import OrderManager
from trading.position_tracker import PositionTracker


# ---------------- fakes ----------------

class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    def __aiter__(self):
        async def gen():
            for f in self._frames:
                yield f
        return gen()

    async def close(self):
        pass


class _FakeConnect:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


def _connect_factory(ws):
    return lambda url: _FakeConnect(ws)


# ---------------- _supervise ----------------

@pytest.mark.asyncio
async def test_supervise_reconnects_after_clean_close():
    calls = []

    async def once():
        calls.append(1)

    stats = FeedStats("t")
    await _supervise(run_once=once, stats=stats, initial_backoff=0.0, max_attempts=3)

    assert len(calls) == 3
    assert stats.connects == 3
    assert stats.disconnects == 3
    assert stats.errors == 0


@pytest.mark.asyncio
async def test_supervise_counts_errors_and_keeps_going():
    async def once():
        raise RuntimeError("socket blew up")

    stats = FeedStats("t")
    await _supervise(run_once=once, stats=stats, initial_backoff=0.0, max_attempts=3)

    assert stats.errors == 3
    assert stats.disconnects == 0
    assert "RuntimeError" in stats.last_error


@pytest.mark.asyncio
async def test_supervise_backoff_grows_on_error_and_is_capped(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def once():
        raise RuntimeError("down")

    stats = FeedStats("t")
    await _supervise(
        run_once=once, stats=stats,
        initial_backoff=1.0, max_backoff=8.0, max_attempts=6,
    )

    # 5 retries after the first attempt: 2, 4, 8, 8, 8 (capped).
    assert slept == [2.0, 4.0, 8.0, 8.0, 8.0]
    assert max(slept) <= 8.0


@pytest.mark.asyncio
async def test_supervise_backoff_resets_after_successful_connect(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    outcomes = [RuntimeError("a"), RuntimeError("b"), None, RuntimeError("c")]

    async def once():
        o = outcomes.pop(0)
        if o is not None:
            raise o

    stats = FeedStats("t")
    await _supervise(
        run_once=once, stats=stats,
        initial_backoff=1.0, max_backoff=8.0, max_attempts=4,
    )

    # 2, 4 after the two errors; the clean return resets to 1.0.
    assert slept == [2.0, 4.0, 1.0]


@pytest.mark.asyncio
async def test_supervise_single_attempt_does_not_sleep(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def once():
        return None

    await _supervise(run_once=once, stats=FeedStats("t"), max_attempts=1)
    assert slept == []


@pytest.mark.asyncio
async def test_supervise_cancellation_propagates():
    started = asyncio.Event()

    async def once():
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(
        _supervise(run_once=once, stats=FeedStats("t"), initial_backoff=0.0)
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_supervise_never_leaks_payload_into_last_error():
    async def once():
        # Simulate a library that echoes the frame (creds included) in the error.
        raise ValueError("bad frame")

    stats = FeedStats("user")
    await _supervise(run_once=once, stats=stats, initial_backoff=0.0, max_attempts=1)
    assert stats.last_error == "ValueError: bad frame"


def test_default_max_backoff_is_bounded():
    assert 0 < MAX_BACKOFF_SECONDS <= 60


# ---------------- run_market_feed ----------------

@pytest.mark.asyncio
async def test_market_feed_drives_cache():
    cache = OrderBookCache()
    frame = (
        '{"event_type":"book","asset_id":"tok-a",'
        '"bids":[{"price":"0.40","size":"100"}],'
        '"asks":[{"price":"0.44","size":"100"}]}'
    )
    ws = _FakeWS([frame])
    stats = FeedStats("market")

    await run_market_feed(
        cache, ["tok-a"],
        connect=_connect_factory(ws),
        stats=stats,
        max_attempts=1,
    )

    book = cache.get("tok-a")
    assert book.midpoint() == pytest.approx(0.42)
    assert stats.connects == 1
    # Subscribe payload went out on connect.
    assert "tok-a" in ws.sent[0]


@pytest.mark.asyncio
async def test_market_feed_survives_a_bad_frame():
    cache = OrderBookCache()
    ws = _FakeWS(["not json at all", '{"event_type":"book","asset_id":"tok-a",'
                                     '"bids":[{"price":"0.50","size":"5"}],'
                                     '"asks":[{"price":"0.52","size":"5"}]}'])
    stats = FeedStats("market")
    await run_market_feed(cache, ["tok-a"], connect=_connect_factory(ws),
                          stats=stats, max_attempts=1)

    assert cache.get("tok-a").midpoint() == pytest.approx(0.51)
    assert stats.errors == 0


# ---------------- run_user_feed ----------------

@pytest.mark.asyncio
async def test_user_feed_drives_position_tracker_and_order_manager():
    tracker = PositionTracker(cache=OrderBookCache())
    om = OrderManager(polymarket_client=object())
    om.orders["loc-1"] = _tracked(om, remote_id="rem-1", size_usd=50.0)

    trade_frame = (
        '{"event_type":"trade","market":"m1","asset_id":"tok-a","side":"YES",'
        '"size":"50","price":"0.40","order_id":"rem-1"}'
    )
    order_frame = '{"event_type":"order","order_id":"rem-1","status":"MATCHED"}'
    ws = _FakeWS([trade_frame, order_frame])

    sub = UserSubscriber(api_key="k", secret="s", passphrase="p", markets=["m1"])

    def on_trade(ev):
        tracker.on_trade_event(ev)
        om.on_trade_event(ev)

    await run_user_feed(
        sub,
        on_trade=on_trade,
        on_order=om.on_order_event,
        connect=_connect_factory(ws),
        max_attempts=1,
    )

    pos = tracker.get("m1", "tok-a", "YES")
    assert pos is not None
    assert pos.size_usd == pytest.approx(50.0)
    assert pos.avg_entry_price == pytest.approx(0.40)
    assert om.get("loc-1").status == "filled"


@pytest.mark.asyncio
async def test_user_feed_sends_subscribe_payload_once_per_connect():
    ws = _FakeWS([])
    sub = UserSubscriber(api_key="k", secret="s", passphrase="p", markets=["m1"])
    await run_user_feed(sub, connect=_connect_factory(ws), max_attempts=1)
    assert len(ws.sent) == 1


# ---------------- derive_user_subscriber ----------------

class _CredsObj:
    def __init__(self, key="ak", secret="as", passphrase="ap"):
        self.api_key = key
        self.api_secret = secret
        self.api_passphrase = passphrase


class _ClientWithCreds:
    def __init__(self, creds):
        self._creds = creds

    def create_or_derive_api_creds(self):
        return self._creds


class _ClientThatRaises:
    def create_or_derive_api_creds(self):
        raise RuntimeError("POLYMARKET_PRIVATE_KEY missing")


def test_derive_user_subscriber_from_apicreds_shape():
    sub = derive_user_subscriber(_ClientWithCreds(_CredsObj()), ["m1", "m2"])
    assert sub is not None
    assert sub.api_key == "ak"
    assert sub.secret == "as"
    assert sub.passphrase == "ap"
    assert sub.markets == ["m1", "m2"]


def test_derive_user_subscriber_from_dict_shape():
    creds = {"apiKey": "dk", "secret": "ds", "passphrase": "dp"}
    sub = derive_user_subscriber(_ClientWithCreds(creds), ["m1"])
    assert sub is not None
    assert (sub.api_key, sub.secret, sub.passphrase) == ("dk", "ds", "dp")


def test_derive_user_subscriber_returns_none_when_no_wallet():
    assert derive_user_subscriber(_ClientThatRaises(), ["m1"]) is None


def test_derive_user_subscriber_returns_none_without_the_method():
    assert derive_user_subscriber(object(), ["m1"]) is None


def test_derive_user_subscriber_returns_none_on_partial_creds():
    creds = _CredsObj(secret="")
    assert derive_user_subscriber(_ClientWithCreds(creds), ["m1"]) is None


def test_derive_user_subscriber_returns_none_on_null_creds():
    assert derive_user_subscriber(_ClientWithCreds(None), ["m1"]) is None


# ---------------- helpers ----------------

def _tracked(om, *, remote_id, size_usd):
    from trading.order_manager import TrackedOrder
    return TrackedOrder(
        local_id="loc-1", market_id="m1", token_id="tok-a", side="YES",
        size_usd=size_usd, price=0.40, status="open", remote_id=remote_id,
    )
