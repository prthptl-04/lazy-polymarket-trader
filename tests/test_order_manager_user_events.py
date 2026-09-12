"""OrderManager user-channel adapters (Phase D).

- Acceptance: order status + fill accumulation update the tracked order.
- Edge: unknown remote id, malformed event, unknown status, zero size.
- Blind: a late event must not resurrect a terminal order into open_orders().
"""

import pytest

from trading.order_manager import OrderManager, TrackedOrder


def _om_with_order(**overrides):
    om = OrderManager(polymarket_client=object())
    defaults = dict(
        local_id="loc-1", market_id="m1", token_id="tok-a", side="YES",
        size_usd=100.0, price=0.40, status="open", remote_id="rem-1",
    )
    defaults.update(overrides)
    om.orders["loc-1"] = TrackedOrder(**defaults)
    return om


# ---------------- by_remote_id ----------------

def test_by_remote_id_finds_the_order():
    om = _om_with_order()
    assert om.by_remote_id("rem-1").local_id == "loc-1"


def test_by_remote_id_returns_none_when_absent():
    om = _om_with_order()
    assert om.by_remote_id("nope") is None


# ---------------- on_order_event ----------------

@pytest.mark.parametrize("remote_status,expected", [
    ("LIVE", "open"),
    ("PLACEMENT", "open"),
    ("MATCHED", "filled"),
    ("CONFIRMED", "filled"),
    ("CANCELED", "cancelled"),
    ("CANCELLED", "cancelled"),
    ("UNMATCHED", "cancelled"),
    ("REJECTED", "rejected"),
])
def test_order_event_status_mapping(remote_status, expected):
    om = _om_with_order()
    out = om.on_order_event({"order_id": "rem-1", "status": remote_status})
    assert out.status == expected


def test_order_event_status_is_case_insensitive():
    om = _om_with_order()
    assert om.on_order_event({"order_id": "rem-1", "status": "matched"}).status == "filled"


def test_order_event_unknown_remote_id_is_ignored():
    om = _om_with_order()
    assert om.on_order_event({"order_id": "other", "status": "MATCHED"}) is None
    assert om.get("loc-1").status == "open"


def test_order_event_unknown_status_is_ignored():
    om = _om_with_order()
    assert om.on_order_event({"order_id": "rem-1", "status": "WAT"}) is None
    assert om.get("loc-1").status == "open"


def test_order_event_without_id_is_ignored():
    om = _om_with_order()
    assert om.on_order_event({"status": "MATCHED"}) is None


def test_order_event_non_dict_is_ignored():
    om = _om_with_order()
    assert om.on_order_event("garbage") is None


def test_late_event_does_not_resurrect_cancelled_order():
    om = _om_with_order(status="cancelled")
    out = om.on_order_event({"order_id": "rem-1", "status": "LIVE"})
    assert out.status == "cancelled"
    assert om.open_orders() == []


def test_late_event_does_not_reopen_filled_order():
    om = _om_with_order(status="filled")
    om.on_order_event({"order_id": "rem-1", "status": "LIVE"})
    assert om.get("loc-1").status == "filled"


def test_order_event_accepts_alternate_id_keys():
    om = _om_with_order()
    assert om.on_order_event({"orderID": "rem-1", "status": "MATCHED"}).status == "filled"


# ---------------- on_trade_event ----------------

def test_partial_fill_marks_partially_filled():
    om = _om_with_order()
    out = om.on_trade_event({"order_id": "rem-1", "size": "40"})
    assert out.filled_size_usd == pytest.approx(40.0)
    assert out.status == "partially_filled"
    assert om.open_orders() == [out]


def test_accumulated_partials_complete_the_fill():
    om = _om_with_order()
    om.on_trade_event({"order_id": "rem-1", "size": "60"})
    out = om.on_trade_event({"order_id": "rem-1", "size": "40"})
    assert out.filled_size_usd == pytest.approx(100.0)
    assert out.status == "filled"
    assert om.open_orders() == []


def test_overfill_is_clamped_to_tracked_size():
    om = _om_with_order()
    out = om.on_trade_event({"order_id": "rem-1", "size": "250"})
    assert out.filled_size_usd == pytest.approx(100.0)
    assert out.status == "filled"


def test_float_dust_still_counts_as_filled():
    om = _om_with_order(size_usd=0.3)
    om.on_trade_event({"order_id": "rem-1", "size": "0.1"})
    om.on_trade_event({"order_id": "rem-1", "size": "0.1"})
    out = om.on_trade_event({"order_id": "rem-1", "size": "0.1"})
    assert out.status == "filled"


def test_trade_event_with_bad_size_is_ignored():
    om = _om_with_order()
    assert om.on_trade_event({"order_id": "rem-1", "size": "abc"}) is None
    assert om.get("loc-1").filled_size_usd == 0.0


def test_trade_event_with_zero_size_is_ignored():
    om = _om_with_order()
    assert om.on_trade_event({"order_id": "rem-1", "size": "0"}) is None


def test_trade_event_missing_size_is_ignored():
    om = _om_with_order()
    assert om.on_trade_event({"order_id": "rem-1"}) is None


def test_trade_event_unknown_order_is_ignored():
    om = _om_with_order()
    assert om.on_trade_event({"order_id": "other", "size": "10"}) is None
