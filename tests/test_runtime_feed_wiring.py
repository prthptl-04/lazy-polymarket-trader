"""Phase D: user-channel + market-channel wiring at the runtime layer.

Roadmap AC: "open the user channel once loop.start() is called."

- Acceptance: GO starts both feeds; a fill arriving on the user channel shows
  up in PositionTracker and in the dashboard's live P&L.
- Edge: no wallet → user feed not attached, GO still works (paper mode).
- Blind: feeds are lazy (nothing connects before GO); STOP cancels them.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import attach_live_feeds, build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from trading.autonomous_loop import WatchedMarket


class _StubClient:
    def post_order(self, order): return {"orderID": "x"}
    def cancel_order(self, order_id): return {"ok": True}


class _WalletClient(_StubClient):
    def create_or_derive_api_creds(self):
        class _C:
            api_key = "CRED-KEY-7f3a"
            api_secret = "CRED-SECRET-9b21"
            api_passphrase = "CRED-PASS-04de"
        return _C()


class _StubStrategy:
    async def submit_or_replace_async(self, token_id, market_id, order_manager):
        class _D:
            kind = "skip"
        return _D()


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
            # Stay open after draining so the supervisor doesn't spin.
            await asyncio.sleep(3600)
        return gen()

    async def close(self):
        pass


class _RecordingConnect:
    """Tracks whether anything actually opened a socket."""

    def __init__(self, ws):
        self.ws = ws
        self.opens = 0

    def __call__(self, url):
        self.opens += 1
        outer = self

        class _CM:
            async def __aenter__(self):
                return outer.ws

            async def __aexit__(self, *a):
                return False

        return _CM()


def _runtime(client, watched=None, memory_path=None, connect=None):
    rt = build_runtime(
        polymarket_client=client,
        watched=watched if watched is not None else [
            WatchedMarket(market_id="m1", token_id="tok-a")
        ],
        memory=MemoryStore(db_path=memory_path) if memory_path else MemoryStore(),
        starting_bankroll_usd=100.0,
    )
    rt.loop.strategy = _StubStrategy()
    rt.loop.tick_interval_seconds = 0.01
    rt.loop.cashout_interval_seconds = 0.01
    rt.loop.status_interval_seconds = 0.01
    if connect is not None:
        attach_live_feeds(rt, client, connect=connect)
    return rt


# ---------------- attachment ----------------

def test_build_runtime_leaves_feeds_unattached_by_default(tmp_path):
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"))
    assert rt.loop.market_ws_task_factory is None
    assert rt.loop.user_ws_task_factory is None
    assert rt.user_feed_attached is False


def test_attach_live_feeds_wires_market_feed_always(tmp_path):
    rt = _runtime(_StubClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _StubClient())
    assert rt.loop.market_ws_task_factory is not None


def test_no_wallet_means_no_user_feed(tmp_path):
    rt = _runtime(_StubClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _StubClient())
    assert rt.loop.user_ws_task_factory is None
    assert rt.user_feed_attached is False


def test_wallet_client_attaches_user_feed(tmp_path):
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _WalletClient())
    assert rt.loop.user_ws_task_factory is not None
    assert rt.user_feed_attached is True


def test_build_runtime_attach_feeds_flag(tmp_path):
    rt = build_runtime(
        polymarket_client=_WalletClient(),
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        attach_feeds=True,
    )
    assert rt.loop.market_ws_task_factory is not None
    assert rt.user_feed_attached is True


def test_attach_is_lazy_nothing_connects_before_go(tmp_path):
    conn = _RecordingConnect(_FakeWS([]))
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"), connect=conn)
    assert conn.opens == 0


def test_user_subscriber_covers_deduped_watched_markets(tmp_path):
    watched = [
        WatchedMarket(market_id="m1", token_id="tok-a"),
        WatchedMarket(market_id="m1", token_id="tok-b"),
        WatchedMarket(market_id="m2", token_id="tok-c"),
    ]
    conn = _RecordingConnect(_FakeWS([]))
    rt = _runtime(_WalletClient(), watched=watched,
                  memory_path=str(tmp_path / "m.db"), connect=conn)
    assert rt.user_feed_attached is True
    # Market list on the subscription is deduped and sorted.
    import json
    payloads = []

    async def drive():
        await rt.loop.start()
        await asyncio.sleep(0.05)
        await rt.loop.stop()

    asyncio.run(drive())
    for sent in conn.ws.sent:
        payloads.append(json.loads(sent))
    user_payload = [p for p in payloads if p.get("type") == "user"]
    assert user_payload and user_payload[0]["markets"] == ["m1", "m2"]


# ---------------- GO opens the channels ----------------

@pytest.mark.asyncio
async def test_go_opens_both_channels(tmp_path):
    conn = _RecordingConnect(_FakeWS([]))
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"), connect=conn)

    await rt.loop.start()
    await asyncio.sleep(0.05)
    try:
        assert conn.opens == 2          # market + user
        assert rt.market_feed_stats.connects == 1
        assert rt.user_feed_stats.connects == 1
    finally:
        await rt.loop.stop()


@pytest.mark.asyncio
async def test_stop_cancels_feed_tasks(tmp_path):
    conn = _RecordingConnect(_FakeWS([]))
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"), connect=conn)

    await rt.loop.start()
    await asyncio.sleep(0.05)
    await rt.loop.stop()

    assert rt.loop.state == "stopped"
    assert rt.loop._tasks == []


@pytest.mark.asyncio
async def test_go_works_with_no_wallet(tmp_path):
    conn = _RecordingConnect(_FakeWS([]))
    rt = _runtime(_StubClient(), memory_path=str(tmp_path / "m.db"), connect=conn)

    await rt.loop.start()
    await asyncio.sleep(0.05)
    try:
        assert rt.loop.state == "running"
        assert conn.opens == 1          # market only
    finally:
        await rt.loop.stop()


# ---------------- live P&L end-to-end ----------------

@pytest.mark.asyncio
async def test_fill_on_user_channel_reaches_pnl(tmp_path):
    book = (
        '{"event_type":"book","asset_id":"tok-a",'
        '"bids":[{"price":"0.50","size":"100"}],'
        '"asks":[{"price":"0.50","size":"100"}]}'
    )
    trade = (
        '{"event_type":"trade","market":"m1","asset_id":"tok-a","side":"YES",'
        '"size":"40","price":"0.40"}'
    )
    conn = _RecordingConnect(_FakeWS([book, trade]))
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"), connect=conn)

    await rt.loop.start()
    await asyncio.sleep(0.05)
    try:
        pnl = rt.pnl()
        assert pnl["open_positions"] == 1
        # Bought 40 USD of YES at 0.40; mid is now 0.50 → +4.00 unrealized.
        assert pnl["unrealized_usd"] == pytest.approx(4.0)
        assert pnl["equity_usd"] == pytest.approx(104.0)

        positions = rt.positions()
        assert positions[0]["market_id"] == "m1"
        assert positions[0]["avg_entry_price"] == pytest.approx(0.40)
    finally:
        await rt.loop.stop()


# ---------------- feeds view + route ----------------

def test_feeds_view_shape(tmp_path):
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _WalletClient())
    feeds = rt.feeds()
    assert feeds["market"]["name"] == "market"
    assert feeds["user"]["attached"] is True
    assert feeds["market"]["connects"] == 0


def test_api_feeds_route(tmp_path):
    rt = _runtime(_StubClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _StubClient())
    c = TestClient(create_app(rt))
    r = c.get("/api/feeds")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["attached"] is False
    assert "connects" in body["market"]


def test_api_feeds_never_exposes_creds(tmp_path):
    rt = _runtime(_WalletClient(), memory_path=str(tmp_path / "m.db"))
    attach_live_feeds(rt, _WalletClient())
    c = TestClient(create_app(rt))
    body = c.get("/api/feeds").text
    for secret in ("CRED-KEY-7f3a", "CRED-SECRET-9b21", "CRED-PASS-04de",
                   "apiKey", "passphrase"):
        assert secret not in body
