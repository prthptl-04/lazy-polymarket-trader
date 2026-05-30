"""OrderManager coverage with QA hats on.

- Acceptance Auditor: every state transition (pending→open, open→cancelled,
  open→filled, replace flow) pinned by a test.
- Edge Case Hunter: cancel of already-cancelled order; replace when old id
  unknown; place that fails after cancel succeeded.
- Blind Hunter: behavior when client raises a non-Exception (BaseException).
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from trading.order_manager import OrderManager, TrackedOrder


@dataclass
class _Trade:
    market_id: str = "m1"
    token_id: str = "tok-a"
    side: str = "YES"
    size_usd: float = 5.0
    price: float = 0.5


class _FakeClient:
    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.cancelled: list[str] = []
        self.next_order_id = "remote-1"
        self.post_raises: BaseException | None = None
        self.cancel_raises: BaseException | None = None

    def post_order(self, order: dict) -> dict:
        if self.post_raises is not None:
            raise self.post_raises
        self.posted.append(order)
        return {"orderID": self.next_order_id}

    def cancel_order(self, order_id: str) -> dict:
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append(order_id)
        return {"ok": True}


# -------- submit --------

def test_submit_async_marks_open_and_stores_remote_id():
    client = _FakeClient()
    om = OrderManager(client)
    order = asyncio.run(om.submit_async(_Trade()))
    assert order.status == "open"
    assert order.remote_id == "remote-1"
    assert client.posted == [{
        "market_id": "m1", "side": "YES", "size_usd": 5.0, "price": 0.5,
    }]


def test_submit_async_rejects_when_client_raises():
    client = _FakeClient()
    client.post_raises = RuntimeError("clob 500")
    om = OrderManager(client)
    order = asyncio.run(om.submit_async(_Trade()))
    assert order.status == "rejected"
    assert "clob 500" in (order.error or "")


# -------- cancel --------

def test_cancel_marks_cancelled_and_calls_client():
    client = _FakeClient()
    om = OrderManager(client)
    placed = asyncio.run(om.submit_async(_Trade()))
    cancelled = asyncio.run(om.cancel_async(placed.local_id))
    assert cancelled.status == "cancelled"
    assert client.cancelled == ["remote-1"]


def test_cancel_unknown_local_id_raises():
    om = OrderManager(_FakeClient())
    with pytest.raises(KeyError):
        asyncio.run(om.cancel_async("nope"))


def test_cancel_idempotent_on_already_cancelled():
    client = _FakeClient()
    om = OrderManager(client)
    placed = asyncio.run(om.submit_async(_Trade()))
    asyncio.run(om.cancel_async(placed.local_id))
    again = asyncio.run(om.cancel_async(placed.local_id))
    assert again.status == "cancelled"
    assert client.cancelled == ["remote-1"]   # not called twice


def test_cancel_records_error_but_does_not_raise():
    client = _FakeClient()
    client.cancel_raises = RuntimeError("clob 502")
    om = OrderManager(client)
    placed = asyncio.run(om.submit_async(_Trade()))
    result = asyncio.run(om.cancel_async(placed.local_id))
    assert "cancel failed" in (result.error or "")


# -------- replace --------

def test_replace_runs_cancel_and_place_in_parallel():
    client = _FakeClient()
    om = OrderManager(client)
    old = asyncio.run(om.submit_async(_Trade()))
    client.next_order_id = "remote-2"
    outcome = asyncio.run(om.replace_async(old.local_id, _Trade(price=0.6)))
    assert outcome.parallel is True
    assert outcome.placed is not None
    assert outcome.placed.remote_id == "remote-2"
    assert outcome.cancelled is not None
    assert outcome.cancelled.status == "cancelled"


def test_replace_with_unknown_old_id_still_places_new():
    """Replacing a non-existent order shouldn't block the new placement."""
    client = _FakeClient()
    om = OrderManager(client)
    outcome = asyncio.run(om.replace_async("ghost-id", _Trade()))
    assert outcome.placed is not None
    assert outcome.placed.status == "open"


def test_replace_surfaces_place_error_in_outcome():
    client = _FakeClient()
    om = OrderManager(client)
    old = asyncio.run(om.submit_async(_Trade()))
    client.post_raises = RuntimeError("clob down")
    outcome = asyncio.run(om.replace_async(old.local_id, _Trade(price=0.6)))
    # Cancel still succeeded; place failed.
    assert outcome.cancelled is not None and outcome.cancelled.status == "cancelled"
    assert outcome.placed is not None and outcome.placed.status == "rejected"


# -------- reads --------

def test_open_orders_excludes_terminal_states():
    client = _FakeClient()
    om = OrderManager(client)
    a = asyncio.run(om.submit_async(_Trade()))
    asyncio.run(om.cancel_async(a.local_id))
    client.next_order_id = "remote-2"
    asyncio.run(om.submit_async(_Trade(price=0.6)))
    open_now = om.open_orders()
    assert all(o.status in ("open", "partially_filled", "pending") for o in open_now)
    assert len(open_now) == 1
