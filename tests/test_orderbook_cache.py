from live_market.orderbook_cache import BookLevel, OrderBookCache
from live_market.rest_snapshot import seed_from_snapshot


def test_book_event_replaces_full_book():
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.39", "size": "200"}],
        "asks": [{"price": "0.41", "size": "150"}, {"price": "0.42", "size": "300"}],
    })
    book = cache.get("tok-a")
    assert book.best_bid() == BookLevel(0.40, 100.0)
    assert book.best_ask() == BookLevel(0.41, 150.0)
    assert book.midpoint() == 0.405
    assert book.spread_bps() == int(round(0.01 / 0.405 * 10_000))


def test_price_change_inserts_then_removes_level():
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.41", "size": "150"}],
    })
    cache.apply_event({"event_type": "price_change", "asset_id": "tok-a",
                       "side": "BID", "price": "0.395", "size": "50"})
    bids = cache.get("tok-a").bids
    assert [b.price for b in bids] == [0.40, 0.395]
    cache.apply_event({"event_type": "price_change", "asset_id": "tok-a",
                       "side": "BID", "price": "0.395", "size": "0"})
    assert [b.price for b in cache.get("tok-a").bids] == [0.40]


def test_last_trade_price_recorded():
    cache = OrderBookCache()
    cache.apply_event({"event_type": "last_trade_price", "asset_id": "tok-a", "price": "0.42"})
    assert cache.get("tok-a").last_trade_price == 0.42


def test_unknown_event_returns_none():
    cache = OrderBookCache()
    assert cache.apply_event({"event_type": "noise", "asset_id": "tok-a"}) is None


def test_event_without_asset_id_returns_none():
    cache = OrderBookCache()
    assert cache.apply_event({"event_type": "book"}) is None


def test_seed_from_snapshot_populates_book():
    cache = OrderBookCache()
    seed_from_snapshot(cache, "tok-a", {
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.41", "size": "150"}],
    })
    book = cache.get("tok-a")
    assert book.midpoint() == 0.405


def test_depth_usd_sums_levels():
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.39", "size": "200"}],
        "asks": [{"price": "0.41", "size": "150"}, {"price": "0.42", "size": "300"}],
    })
    book = cache.get("tok-a")
    assert book.depth_usd("YES", levels=5) == 300.0
    assert book.depth_usd("NO", levels=5) == 450.0
