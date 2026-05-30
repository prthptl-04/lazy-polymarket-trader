"""Position tracker — happy / boundary / negative coverage.

QA hats:
- Acceptance Auditor: every test pins one entry in the PositionTracker contract.
- Edge Case Hunter: zero size, negative price, decimal-precision close-out.
- Blind Hunter: side mismatch via on_trade_event with malformed payloads.
"""

import math

import pytest

from live_market.orderbook_cache import OrderBookCache
from trading.position_tracker import Position, PositionTracker


# -------- apply_fill --------

def test_apply_fill_opens_new_position():
    t = PositionTracker()
    pos = t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    assert pos.size_usd == 10
    assert pos.avg_entry_price == 0.4
    assert pos.is_open


def test_apply_fill_share_weighted_average():
    t = PositionTracker()
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.6)
    pos = t.get("m1", "tok-a", "YES")
    assert math.isclose(pos.avg_entry_price, 0.5, abs_tol=1e-9)
    assert pos.size_usd == 20


def test_apply_fill_rejects_zero_size():
    t = PositionTracker()
    with pytest.raises(ValueError):
        t.apply_fill(market_id="m1", token_id="t", side="YES", size_usd=0, price=0.5)


def test_apply_fill_rejects_negative_size():
    t = PositionTracker()
    with pytest.raises(ValueError):
        t.apply_fill(market_id="m1", token_id="t", side="YES", size_usd=-1, price=0.5)


@pytest.mark.parametrize("bad_price", [0.0, 1.0, -0.5, 1.5])
def test_apply_fill_rejects_out_of_range_price(bad_price):
    t = PositionTracker()
    with pytest.raises(ValueError):
        t.apply_fill(market_id="m1", token_id="t", side="YES", size_usd=1, price=bad_price)


# -------- unrealized P&L --------

def test_unrealized_pnl_yes_winner():
    p = Position(market_id="m", token_id="t", side="YES", size_usd=10, avg_entry_price=0.4)
    # mid moves up to 0.5 → unrealized = 10 × (0.5 - 0.4) = 1.0
    assert math.isclose(p.unrealized_pnl_usd(0.5), 1.0, abs_tol=1e-9)


def test_unrealized_pnl_yes_loser():
    p = Position(market_id="m", token_id="t", side="YES", size_usd=10, avg_entry_price=0.4)
    assert math.isclose(p.unrealized_pnl_usd(0.3), -1.0, abs_tol=1e-9)


def test_unrealized_pnl_no_side():
    # NO at 0.6 (stored as the NO-side price). YES mid = 0.3 → NO = 0.7.
    p = Position(market_id="m", token_id="t", side="NO", size_usd=10, avg_entry_price=0.6)
    assert math.isclose(p.unrealized_pnl_usd(0.3), 10 * (0.7 - 0.6), abs_tol=1e-9)


def test_unrealized_pnl_closed_position_is_zero():
    p = Position(market_id="m", token_id="t", side="YES", size_usd=0, avg_entry_price=0.0)
    assert p.unrealized_pnl_usd(0.5) == 0.0


# -------- close --------

def test_close_full_position_realizes_pnl():
    t = PositionTracker()
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    pos = t.close(market_id="m1", token_id="tok-a", side="YES", size_usd=10, exit_price=0.5)
    assert pos.size_usd == 0
    assert math.isclose(pos.realized_pnl_usd, 1.0, abs_tol=1e-9)


def test_close_partial_keeps_remainder():
    t = PositionTracker()
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    pos = t.close(market_id="m1", token_id="tok-a", side="YES", size_usd=3, exit_price=0.5)
    assert pos.size_usd == 7
    assert math.isclose(pos.realized_pnl_usd, 0.3, abs_tol=1e-9)


def test_close_more_than_open_size_capped():
    t = PositionTracker()
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    pos = t.close(market_id="m1", token_id="tok-a", side="YES", size_usd=999, exit_price=0.5)
    # Realizes P&L only on the open 10, not on the requested 999.
    assert math.isclose(pos.realized_pnl_usd, 1.0, abs_tol=1e-9)
    assert pos.size_usd == 0


def test_close_unknown_position_raises():
    with pytest.raises(KeyError):
        PositionTracker().close(market_id="m", token_id="t", side="YES", size_usd=1, exit_price=0.5)


# -------- portfolio reads with cache --------

def test_total_unrealized_pnl_sums_open_positions():
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.495", "size": "1000"}],
        "asks": [{"price": "0.505", "size": "1000"}],
    })
    t = PositionTracker(cache=cache)
    t.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.4)
    # mid ≈ 0.5, entry 0.4 → +0.1 per dollar × 10 = +$1
    assert math.isclose(t.total_unrealized_pnl_usd(), 1.0, abs_tol=1e-6)


def test_total_unrealized_pnl_zero_without_cache():
    t = PositionTracker()  # cache=None
    t.apply_fill(market_id="m", token_id="t", side="YES", size_usd=10, price=0.4)
    assert t.total_unrealized_pnl_usd() == 0.0


# -------- on_trade_event adapter --------

def test_on_trade_event_happy():
    t = PositionTracker()
    pos = t.on_trade_event({
        "market": "m1", "asset_id": "tok-a", "side": "YES",
        "size": "10.0", "price": "0.4",
    })
    assert pos is not None and pos.size_usd == 10


def test_on_trade_event_returns_none_on_malformed():
    t = PositionTracker()
    assert t.on_trade_event({"market": "m1"}) is None
    assert t.on_trade_event({}) is None
    assert t.on_trade_event({"market": "m1", "size": "abc"}) is None
